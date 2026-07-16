"""
CMMS Fire Maintenance — FastAPI Backend v1.0
PT ODG Indonesia | PROTK-FFM Gresik
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
import psycopg2
import psycopg2.extras
import os

app = FastAPI(
    title="CMMS Fire API",
    description="PT ODG Indonesia - Fire Protection Maintenance",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DATABASE ─────────────────────────────────────────────────
def get_db():
   import urllib.parse as up
url = up.urlparse(os.environ["DATABASE_URL"])
conn = psycopg2.connect(
    host=url.hostname,
    port=url.port,
    dbname=url.path[1:],
    user=url.username,
    password=up.unquote(url.password),
    sslmode="require",
    cursor_factory=psycopg2.extras.RealDictCursor
)

# ── MODELS ───────────────────────────────────────────────────
class WorkOrderCreate(BaseModel):
    eq_id       : int
    wo_type     : str = "PM"
    planned_start: datetime
    planned_end : datetime
    assigned_to : Optional[str] = "Muhtadi"
    supervisor  : Optional[str] = None
    work_desc   : Optional[str] = None
    priority    : int = 2

class WorkOrderUpdate(BaseModel):
    status      : Optional[str] = None
    actual_start: Optional[datetime] = None
    actual_end  : Optional[datetime] = None
    assigned_to : Optional[str] = None
    findings    : Optional[str] = None
    remarks     : Optional[str] = None
    scan_pdf_url: Optional[str] = None

# ── ROOT ─────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "app"    : "CMMS Fire API",
        "company": "PT ODG Indonesia",
        "project": "PROTK-FFM Gresik",
        "version": "1.0.0",
        "status" : "running"
    }

# ── HEALTH CHECK ─────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) AS total FROM equipment")
        result = cur.fetchone()
        conn.close()
        return {"status": "ok", "equipment_count": result["total"]}
    except Exception as e:
        raise HTTPException(500, f"Database error: {e}")

# ── EQUIPMENT ─────────────────────────────────────────────────
@app.get("/equipment")
def list_equipment(
    area    : Optional[str] = Query(None, description="Smelter / Desal / PMR"),
    category: Optional[str] = Query(None),
    building: Optional[str] = Query(None),
    search  : Optional[str] = Query(None, description="Search tag_id or description"),
    limit   : int = Query(100, le=500),
    offset  : int = Query(0)
):
    conn = get_db()
    cur  = conn.cursor()

    where = ["e.is_active = TRUE"]
    params = []

    if area:
        where.append("e.area = %s")
        params.append(area)
    if category:
        where.append("e.category = %s")
        params.append(category)
    if building:
        where.append("e.building ILIKE %s")
        params.append(f"%{building}%")
    if search:
        where.append("(e.tag_id ILIKE %s OR e.description ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    sql = f"""
        SELECT e.eq_id, e.tag_id, e.description, e.category,
               e.area, e.building, e.is_active,
               mp.interval_days, mp.frequency
        FROM equipment e
        LEFT JOIN maintenance_plan mp ON mp.eq_id = e.eq_id AND mp.is_active = TRUE
        WHERE {' AND '.join(where)}
        ORDER BY e.area, e.building, e.tag_id
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    cur.execute(sql, params)
    rows = cur.fetchall()

    cur.execute(f"SELECT COUNT(*) AS c FROM equipment e WHERE {' AND '.join(where)}", params[:-2])
    total = cur.fetchone()["c"]

    conn.close()
    return {"total": total, "limit": limit, "offset": offset, "data": rows}

@app.get("/equipment/{tag_id}")
def get_equipment(tag_id: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT e.*, mp.plan_code, mp.frequency, mp.interval_days
        FROM equipment e
        LEFT JOIN maintenance_plan mp ON mp.eq_id = e.eq_id AND mp.is_active = TRUE
        WHERE e.tag_id = %s
    """, (tag_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"Equipment {tag_id} tidak ditemukan")
    return row

# ── WORK ORDER ────────────────────────────────────────────────
@app.get("/work-orders")
def list_work_orders(
    area    : Optional[str] = Query(None),
    status  : Optional[str] = Query(None),
    wo_type : Optional[str] = Query(None),
    month   : Optional[str] = Query(None, description="Format: 2026-07"),
    limit   : int = Query(50, le=200),
    offset  : int = Query(0)
):
    conn = get_db()
    cur  = conn.cursor()

    where  = ["1=1"]
    params = []

    if area:
        where.append("e.area = %s")
        params.append(area)
    if status:
        where.append("wo.status = %s")
        params.append(status)
    if wo_type:
        where.append("wo.wo_type = %s")
        params.append(wo_type)
    if month:
        where.append("TO_CHAR(wo.planned_start, 'YYYY-MM') = %s")
        params.append(month)

    sql = f"""
        SELECT wo.wo_id, wo.wo_number, wo.wo_type, wo.status, wo.priority,
               wo.planned_start, wo.planned_end, wo.actual_start, wo.actual_end,
               wo.assigned_to, wo.findings, wo.remarks, wo.scan_pdf_url,
               e.tag_id, e.description, e.area, e.building, e.category
        FROM work_order wo
        JOIN equipment e ON e.eq_id = wo.eq_id
        WHERE {' AND '.join(where)}
        ORDER BY wo.planned_start DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    return {"total": len(rows), "data": rows}

@app.get("/work-orders/{wo_number}")
def get_work_order(wo_number: str):
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        SELECT wo.*, e.tag_id, e.description, e.area, e.building, e.category
        FROM work_order wo
        JOIN equipment e ON e.eq_id = wo.eq_id
        WHERE wo.wo_number = %s
    """, (wo_number,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, f"WO {wo_number} tidak ditemukan")
    return row

@app.post("/work-orders", status_code=201)
def create_work_order(body: WorkOrderCreate):
    conn = get_db()
    cur  = conn.cursor()

    # Cek equipment ada
    cur.execute("SELECT eq_id, area FROM equipment WHERE eq_id = %s", (body.eq_id,))
    eq = cur.fetchone()
    if not eq:
        conn.close()
        raise HTTPException(404, f"Equipment id {body.eq_id} tidak ditemukan")

    # Generate WO number
    cur.execute("SELECT generate_wo_number(%s, %s)", (eq["area"], body.wo_type))
    wo_number = cur.fetchone()["generate_wo_number"]

    cur.execute("""
        INSERT INTO work_order
            (wo_number, wo_type, eq_id, status, priority,
             planned_start, planned_end, assigned_to, work_desc, created_by)
        VALUES (%s, %s, %s, 'CREATED', %s, %s, %s, %s, %s, %s)
        RETURNING wo_id, wo_number, status, planned_start
    """, (wo_number, body.wo_type, body.eq_id, body.priority,
          body.planned_start, body.planned_end,
          body.assigned_to, body.work_desc, "system"))

    result = cur.fetchone()
    conn.commit()
    conn.close()
    return {"message": "Work Order berhasil dibuat", "data": result}

@app.patch("/work-orders/{wo_number}")
def update_work_order(wo_number: str, body: WorkOrderUpdate):
    conn = get_db()
    cur  = conn.cursor()

    # Cek WO ada
    cur.execute("SELECT wo_id, status FROM work_order WHERE wo_number = %s", (wo_number,))
    wo = cur.fetchone()
    if not wo:
        conn.close()
        raise HTTPException(404, f"WO {wo_number} tidak ditemukan")

    # Build update
    sets   = []
    params = []

    if body.status:
        sets.append("status = %s"); params.append(body.status)
        if body.status == "CLOSED":
            sets.append("closed_at = NOW()")
    if body.actual_start:
        sets.append("actual_start = %s"); params.append(body.actual_start)
    if body.actual_end:
        sets.append("actual_end = %s"); params.append(body.actual_end)
    if body.assigned_to:
        sets.append("assigned_to = %s"); params.append(body.assigned_to)
    if body.findings:
        sets.append("findings = %s"); params.append(body.findings)
    if body.remarks:
        sets.append("remarks = %s"); params.append(body.remarks)
    if body.scan_pdf_url:
        sets.append("scan_pdf_url = %s"); params.append(body.scan_pdf_url)

    if not sets:
        conn.close()
        return {"message": "Tidak ada yang diupdate"}

    params.append(wo_number)
    cur.execute(f"""
        UPDATE work_order SET {', '.join(sets)}
        WHERE wo_number = %s
        RETURNING wo_number, status, updated_at
    """, params)

    result = cur.fetchone()
    conn.commit()
    conn.close()
    return {"message": "Work Order diupdate", "data": result}

# ── KPI DASHBOARD ─────────────────────────────────────────────
@app.get("/kpi/compliance")
def kpi_compliance(month: Optional[str] = Query(None, description="Format: 2026-07")):
    conn = get_db()
    cur  = conn.cursor()

    if month:
        cur.execute("""
            SELECT area, COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status='CLOSED') AS closed,
                   ROUND(COUNT(*) FILTER (WHERE status='CLOSED') * 100.0
                   / NULLIF(COUNT(*),0), 1) AS compliance_pct
            FROM work_order wo
            JOIN equipment e ON e.eq_id = wo.eq_id
            WHERE wo.wo_type = 'PM'
              AND TO_CHAR(wo.planned_start,'YYYY-MM') = %s
            GROUP BY e.area ORDER BY e.area
        """, (month,))
    else:
        cur.execute("SELECT * FROM vw_pm_compliance_monthly LIMIT 36")

    rows = cur.fetchall()
    conn.close()
    return {"data": rows}

@app.get("/kpi/overdue")
def kpi_overdue():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM vw_overdue_wo")
    rows = cur.fetchall()
    conn.close()
    return {"total": len(rows), "data": rows}

@app.get("/kpi/summary")
def kpi_summary():
    conn = get_db()
    cur  = conn.cursor()

    cur.execute("""
        SELECT
            (SELECT COUNT(*) FROM equipment WHERE is_active=TRUE) AS total_equipment,
            (SELECT COUNT(*) FROM work_order
             WHERE TO_CHAR(planned_start,'YYYY-MM') = TO_CHAR(NOW(),'YYYY-MM')) AS wo_this_month,
            (SELECT COUNT(*) FROM work_order
             WHERE status='CLOSED'
             AND TO_CHAR(planned_start,'YYYY-MM') = TO_CHAR(NOW(),'YYYY-MM')) AS wo_closed,
            (SELECT COUNT(*) FROM work_order
             WHERE status NOT IN ('CLOSED','CANCELLED')
             AND planned_start < NOW() - INTERVAL '7 days') AS overdue,
            (SELECT COUNT(*) FROM notification WHERE status='OPEN') AS open_notifications
    """)
    row = cur.fetchone()
    conn.close()
    return row
