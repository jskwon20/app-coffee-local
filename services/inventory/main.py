from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import mysql.connector
import os
import httpx
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor


app = FastAPI()

# --- OpenTelemetry Setup ---
resource = Resource.create(attributes={"service.name": "inventory-service"})
trace.set_tracer_provider(TracerProvider(resource=resource))
otlp_exporter = OTLPSpanExporter(endpoint="http://tempo.monitoring:4318/v1/traces")
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

FastAPIInstrumentor.instrument_app(app)
HTTPXClientInstrumentor().instrument()
# ---------------------------


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "password"),
    "database": os.getenv("DB_NAME", "coffee_machine"),
    "charset": "utf8mb4"
}

BILLING_SERVICE = os.getenv("BILLING_SERVICE_URL", "http://localhost:8002")

class UseInventoryRequest(BaseModel):
    coffee_beans: int = 0
    water: int = 0
    milk: int = 0

class AddInventoryRequest(BaseModel):
    item: str
    amount: int

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/inventory")
def get_inventory():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM inventory ORDER BY id DESC LIMIT 1")
    inventory = cursor.fetchone()
    cursor.close()
    conn.close()
    return inventory

@router.post("/inventory/use")
def use_inventory(request: UseInventoryRequest):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM inventory ORDER BY id DESC LIMIT 1")
    inventory = cursor.fetchone()
    
    if (inventory["coffee_beans"] < request.coffee_beans or
        inventory["water"] < request.water or
        inventory["milk"] < request.milk):
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="재고가 부족합니다")
    
    cursor.execute(
        "UPDATE inventory SET coffee_beans = coffee_beans - %s, water = water - %s, milk = milk - %s WHERE id = %s",
        (request.coffee_beans, request.water, request.milk, inventory["id"])
    )
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": "재고 차감 완료"}

@router.post("/inventory/add")
async def add_inventory(request: AddInventoryRequest):
    if request.item not in ["coffee_beans", "water", "milk"]:
        raise HTTPException(status_code=400, detail="잘못된 재고 항목입니다")
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute(f"UPDATE inventory SET {request.item} = {request.item} + %s", (request.amount,))
    conn.commit()
    
    # 재고 추가 비용 처리
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{BILLING_SERVICE}/inventory-cost",
            json={"item": request.item}
        )
    
    cursor.close()
    conn.close()
    
    return {"message": f"{request.item} {request.amount}만큼 추가 완료"}

app.include_router(router, prefix=os.getenv("ROOT_PATH", ""))
