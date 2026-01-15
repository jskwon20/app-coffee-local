from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import mysql.connector
import os
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.mysql import MySQLInstrumentor

app = FastAPI()

# --- OpenTelemetry Setup ---
resource = Resource.create(attributes={"service.name": "billing-service"})
trace.set_tracer_provider(TracerProvider(resource=resource))
otlp_exporter = OTLPSpanExporter(endpoint="http://tempo.monitoring:4318/v1/traces")
span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

FastAPIInstrumentor.instrument_app(app)
MySQLInstrumentor().instrument()
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

class PaymentRequest(BaseModel):
    menu_id: int
    amount: int
    quantity: int
    total_price: int

class InventoryCostRequest(BaseModel):
    item: str

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/sales")
def get_sales():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM billing ORDER BY id DESC LIMIT 1")
    billing = cursor.fetchone()
    cursor.close()
    conn.close()
    
    net_profit = billing["total_sales"] - billing["inventory_cost"]
    return {
        "cash_register": billing["cash_register"],
        "total_sales": billing["total_sales"],
        "inventory_cost": billing["inventory_cost"],
        "net_profit": net_profit
    }

@router.post("/payment")
def process_payment(request: PaymentRequest):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM billing ORDER BY id DESC LIMIT 1")
    billing = cursor.fetchone()
    
    if request.amount < request.total_price:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="금액이 부족합니다")
    
    change = request.amount - request.total_price
    
    if change > billing["cash_register"]:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="거스름돈이 부족합니다")
    
    new_cash = billing["cash_register"] + request.amount - change
    new_sales = billing["total_sales"] + request.total_price
    
    cursor.execute(
        "UPDATE billing SET cash_register = %s, total_sales = %s WHERE id = %s",
        (new_cash, new_sales, billing["id"])
    )
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"change": change, "message": "결제 완료"}

@router.post("/inventory-cost")
def add_inventory_cost(request: InventoryCostRequest):
    costs = {"coffee_beans": 3000, "milk": 2000, "water": 1000}
    cost = costs.get(request.item, 0)
    
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM billing ORDER BY id DESC LIMIT 1")
    billing = cursor.fetchone()
    
    new_cash = billing["cash_register"] - cost
    new_inventory_cost = billing["inventory_cost"] + cost
    
    cursor.execute(
        "UPDATE billing SET cash_register = %s, inventory_cost = %s WHERE id = %s",
        (new_cash, new_inventory_cost, billing["id"])
    )
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": "재고 비용 처리 완료"}

app.include_router(router, prefix=os.getenv("ROOT_PATH", ""))