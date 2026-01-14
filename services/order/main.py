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
resource = Resource.create(attributes={"service.name": "order-service"})
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

INVENTORY_SERVICE = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8001") + "/api/inventory"
BILLING_SERVICE = os.getenv("BILLING_SERVICE_URL", "http://localhost:8002") + "/api/billing"

class OrderRequest(BaseModel):
    menu_id: int
    quantity: int = 1
    payment_amount: int

def get_db():
    return mysql.connector.connect(**DB_CONFIG)

@router.get("/health")
def health():
    return {"status": "ok"}

@router.get("/menu")
def get_menu():
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM menus")
    menus = cursor.fetchall()
    cursor.close()
    conn.close()
    return {"menus": menus}

@router.post("/orders")
async def create_order(order: OrderRequest):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM menus WHERE id = %s", (order.menu_id,))
    menu = cursor.fetchone()
    if not menu:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="메뉴를 찾을 수 없습니다")
    
    total_price = menu["price"] * order.quantity
    
    async with httpx.AsyncClient() as client:
        # 재고 확인 및 차감
        inventory_response = await client.post(
            f"{INVENTORY_SERVICE}/inventory/use",
            json={
                "coffee_beans": menu["coffee_beans"] * order.quantity,
                "water": menu["water"] * order.quantity,
                "milk": menu["milk"] * order.quantity
            }
        )
        
        if inventory_response.status_code != 200:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="재고가 부족합니다")
        
        # 결제 처리
        payment_response = await client.post(
            f"{BILLING_SERVICE}/payment",
            json={
                "menu_id": order.menu_id,
                "amount": order.payment_amount,
                "quantity": order.quantity,
                "total_price": total_price
            }
        )
        
        if payment_response.status_code != 200:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail=payment_response.json().get("detail"))
    
    cursor.execute(
        "INSERT INTO orders (menu_id, quantity, total_price) VALUES (%s, %s, %s)",
        (order.menu_id, order.quantity, total_price)
    )
    conn.commit()
    order_id = cursor.lastrowid
    cursor.close()
    conn.close()
    
    return {
        "order_id": order_id,
        "menu_name": menu["name"],
        "quantity": order.quantity,
        "total_price": total_price,
        "change": payment_response.json()["change"]
    }

app.include_router(router, prefix=os.getenv("ROOT_PATH", ""))
