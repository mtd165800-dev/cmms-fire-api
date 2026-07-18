"""
CMMS Fire Maintenance — FastAPI Backend v1.1
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
    version="1.1.0"
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
        port=url.port or 5432,
        dbname=url.path[1:],
        user=url.username,
        password=up.unquote(url.password or ""),
        sslmode="require",
        cursor_factory=psycopg2.extras.RealDictCursor
    )
    return conn

# ── MODELS ───────────────────────────────────────────────────
class WorkOrderCreate(BaseModel):
    eq_id        : int
    wo_type      : str = "PM"
    planned_start: datetime
    planned_end  : datetime
    assigned_to  : Optional[str] = "Muhtadi"
    supervisor   : Optional[str] = None
    work_desc    : Optional[str] = None
    priority     : int = 2

class WorkOrderUpdate(BaseModel):
    status      : Optional[str] = None
    actual_start: Optional[datetime] = None
    actual_end  : Optional[datetime] = None
    assigned_to : Optional[str] = None
    findings    : Optional[str] = None
    remarks     : Optional[str] = None
    scan_pdf_url: Optional[str] = None

class EquipmentCreate(BaseModel):
    tag_id              : str
    description         : str
    category            : str
    area                : str
    building            : str
    functional_location : Optional[str] = None
    is_active           : Optional[bool] = True

class EquipmentUpdate(BaseModel):
    description         : Optional[str] = None
    category            : Optional[str] = None
    area                : Optional[str] = None
    building            : Optional[str] = None
    functional_location : Optional[str] = None
    is_active           : Optional[bool] = None

class BuildingRename(BaseModel):
    area    : str
    old_name: str
    new_name: str

class MaintenancePlanCreate(BaseModel):
    eq_id               : str
    plan_code           : Optional[str] = None
    frequency           : str
    interval_days       : int
    last_completed_date : Optional[date] = None

# ── ROOT ─────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "app"    : "CMMS Fire API",
        "company": "PT ODG Indonesia",
        "project": "PROTK-FFM Gresik",
        "version": "1.1.0",
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
    area    : Optional[str] = Query(None, description="Smelter / Desalination / PMR"),
    category: Optional[str] = Query(None),
    building: Optional[str] = Query(None),
    search  : Optional[str] = Query(None, description="Search tag_id or description"),
    limit   : int = Query(100, le=500),
    offset  : int = Query(0)
):
    conn = get_db()
    cur  = conn.cursor()

    where  = ["e.is_active = TRUE"]
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

@app.post("/equipment", status_code=201)
def create_equipment(body: EquipmentCreate):
    valid_categories = [
        "Fire Detection", "Fire Extinguisher", "Fire Water System",
        "Emergency Shower & Eyewash", "Clean Agent System"
    ]
    valid_areas = ["Smelter", "PMR", "Desalination"]

    tag_id = body.tag_id.strip().upper()

    if body.category not in valid_categories:
        raise HTTPException(400, f"Category tidak valid. Pilihan: {valid_categories}")
    if body.area not in valid_areas:
        raise HTTPException(400, f"Area tidak valid. Pilihan: {valid_areas}")

    conn = get_db()
    cur  = conn.cursor()

    cur.execute("SELECT tag_id FROM equipment WHERE tag_id = %s", (tag_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(400, f"Tag ID '{tag_id}' sudah ada di database")

    cur.execute("""
        INSERT INTO equipment
            (tag_id, description, category, area, building, functional_location, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING eq_id, tag_id, description, category, area, building, is_active
    """, (
        tag_id,
        body.description.strip(),
        body.category,
        body.area,
        body.building.strip(),
        body.functional_location.strip() if body.functional_location else None,
        body.is_active if body.is_active is not None else True
    ))

    result = cur.fetchone()
    conn.commit()
    conn.close()
    return {"message": "Equipment berhasil ditambahkan", "data": result}

@app.patch("/equipment/{tag_id}")
def update_equipment(tag_id: str, body: EquipmentUpdate):
    tag_id = tag_id.upper()

    valid_categories = [
        "Fire Detection", "Fire Extinguisher", "Fire Water System",
        "Emergency Shower & Eyewash", "Clean Agent System"
    ]
    valid_areas = ["Smelter", "PMR", "Desalination"]

    if body.category and body.category not in valid_categories:
        raise HTTPException(400, f"Category tidak valid. Pilihan: {valid_categories}")
    if body.area and body.area not in valid_areas:
        raise HTTPException(400, f"Area tidak valid. Pilihan: {valid_areas}")

    conn = get_db()
    cur  = conn.cursor()

    cur.execute("SELECT tag_id FROM equipment WHERE tag_id = %s", (tag_id,))
    if not cur.fetchone():
        conn.close()
        raise HTTPException(404, f"Equipment '{tag_id}' tidak ditemukan")

    sets   = []
    params = []

    if body.description is not None:
        sets.append("description = %s"); params.append(body.description.strip())
    if body.category is not None:
        sets.append("category = %s"); params.append(body.category)
    if body.area is not None:
        sets.append("area = %s"); params.append(body.area)
    if body.building is not None:
        sets.append("building = %s"); params.append(body.building.strip())
    if body.functional_location is not None:
        sets.append("functional_location = %s"); params.append(body.functional_location.strip())
    if body.is_active is not None:
        sets.append("is_active = %s"); params.append(body.is_active)

    if not sets:
        conn.close()
        return {"message": "Tidak ada data yang diupdate"}

    params.append(tag_id)
    cur.execute(f"""
        UPDATE equipment SET {', '.join(sets)}
        WHERE tag_id = %s
        RETURNING eq_id, tag_id, description, category, area, building, is_active
    """, params)

    result = cur.fetchone()
    conn.commit()
    conn.close()
    return {"message": "Equipment berhasil diupdate", "data": result}

@app.delete("/equipment/{tag_id}")
def delete_equipment(tag_id: str):
    tag_id = tag_id.upper()

    conn = get_db()
    cur  = conn.cursor()

    cur.execute("SELECT eq_id FROM equipment WHERE tag_id = %s", (tag_id,))
    eq = cur.fetchone()
    if not eq:
        conn.close()
        raise HTTPException(404, f"Equipment '{tag_id}' tidak ditemukan")

    # Cek WO aktif
    cur.execute("""
        SELECT wo_number FROM work_order
        WHERE eq_id = %s AND status NOT IN ('CLOSED', 'CANCELLED')
        LIMIT 5
    """, (eq["eq_id"],))
    active_wos = cur.fetchall()

    if active_wos:
        wo_list = [w["wo_number"] for w in active_wos]
        conn.close()
        raise HTTPException(400,
            f"Tidak bisa hapus — ada WO aktif: {', '.join(wo_list)}"
        )

    # Hapus maintenance_plan dulu (FK constraint)
    cur.execute("DELETE FROM maintenance_plan WHERE eq_id = %s", (eq["eq_id"],))
    cur.execute("DELETE FROM equipment WHERE tag_id = %s", (tag_id,))

    conn.commit()
    conn.close()
    return {"message": f"Equipment '{tag_id}' dan maintenance plan-nya berhasil dihapus"}

# ── MAINTENANCE PLAN ──────────────────────────────────────────
@app.post("/maintenance-plans", status_code=201)
def create_maintenance_plan(body: MaintenancePlanCreate):
    valid_freq = ["Weekly", "Two_Weekly", "Monthly"]

    eq_id = body.eq_id.strip().upper()

    if body.frequency not in valid_freq:
        raise HTTPException(400, f"Frequency tidak valid. Pilihan: {valid_freq}")

    conn = get_db()
    cur  = conn.cursor()

    cur.execute("SELECT eq_id FROM equipment WHERE tag_id = %s", (eq_id,))
    eq = cur.fetchone()
    if not eq:
        conn.close()
        raise HTTPException(404, f"Equipment '{eq_id}' tidak ditemukan")

    eq_pk = eq["eq_id"]

    cur.execute(
        "SELECT plan_code FROM maintenance_plan WHERE eq_id = %s AND is_active = TRUE",
        (eq_pk,)
    )
    existing = cur.fetchone()
    if existing:
        conn.close()
        raise HTTPException(400,
            f"Equipment '{eq_id}' sudah punya maintenance plan: {existing['plan_code']}"
        )

    plan_code = body.plan_code.strip() if body.plan_code else f"PM-{eq_id}"

    cur.execute("""
        INSERT INTO maintenance_plan
            (plan_code, eq_id, frequency, interval_days, last_completed_date, is_active)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        RETURNING plan_code, eq_id, frequency, interval_days, last_completed_date
    """, (plan_code, eq_pk, body.frequency, body.interval_days, body.last_completed_date))

    result = cur.fetchone()
    conn.commit()
    conn.close()
    return {"message": "Maintenance plan berhasil ditambahkan", "data": result}

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

    cur.execute("SELECT eq_id, area FROM equipment WHERE eq_id = %s", (body.eq_id,))
    eq = cur.fetchone()
    if not eq:
        conn.close()
        raise HTTPException(404, f"Equipment id {body.eq_id} tidak ditemukan")

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

    cur.execute("SELECT wo_id, status FROM work_order WHERE wo_number = %s", (wo_number,))
    wo = cur.fetchone()
    if not wo:
        conn.close()
        raise HTTPException(404, f"WO {wo_number} tidak ditemukan")

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

# ── BULK OPERATIONS ───────────────────────────────────────────
@app.get("/equipment/by-building")
def equipment_by_building(area: Optional[str] = Query(None)):
    """Kembalikan semua equipment digroup per building"""
    conn = get_db()
    cur  = conn.cursor()

    where  = ["e.is_active = TRUE"]
    params = []
    if area:
        where.append("e.area = %s")
        params.append(area)

    cur.execute(f"""
        SELECT e.eq_id, e.tag_id, e.description, e.category,
               e.area, e.building,
               mp.interval_days, mp.frequency,
               (SELECT wo.status FROM work_order wo
                WHERE wo.eq_id = e.eq_id
                ORDER BY wo.created_at DESC LIMIT 1) AS latest_wo_status,
               (SELECT wo.wo_number FROM work_order wo
                WHERE wo.eq_id = e.eq_id
                ORDER BY wo.created_at DESC LIMIT 1) AS latest_wo_number
        FROM equipment e
        LEFT JOIN maintenance_plan mp ON mp.eq_id = e.eq_id AND mp.is_active = TRUE
        WHERE {' AND '.join(where)}
        ORDER BY e.building, e.category, e.tag_id
    """, params)

    rows = cur.fetchall()
    conn.close()

    from collections import defaultdict
    buildings = defaultdict(list)
    for r in rows:
        buildings[r["building"]].append(r)

    result = []
    for bldg, equips in sorted(buildings.items()):
        systems = defaultdict(list)
        for eq in equips:
            systems[eq["category"] or "Other"].append(eq)

        result.append({
            "building"  : bldg,
            "total_eq"  : len(equips),
            "open_wo"   : sum(1 for e in equips if e["latest_wo_status"] not in ["CLOSED","CANCELLED"] and e["latest_wo_status"] is not None),
            "closed_wo" : sum(1 for e in equips if e["latest_wo_status"] == "CLOSED"),
            "no_wo"     : sum(1 for e in equips if e["latest_wo_status"] is None),
            "systems"   : [
                {"category": cat, "equipment": list(eqs)}
                for cat, eqs in sorted(systems.items())
            ]
        })

    return {"area": area, "buildings": result, "total_buildings": len(result)}

@app.post("/work-orders/bulk-close")
def bulk_close_wo(body: dict):
    """Close semua WO open untuk building tertentu"""
    building    = body.get("building")
    area        = body.get("area")
    actual_end  = body.get("actual_end")
    assigned_to = body.get("assigned_to", "Muhtadi")

    if not building:
        raise HTTPException(400, "building harus diisi")

    conn = get_db()
    cur  = conn.cursor()

    cur.execute("""
        SELECT wo.wo_id, wo.wo_number
        FROM work_order wo
        JOIN equipment e ON e.eq_id = wo.eq_id
        WHERE e.building = %s
          AND (e.area = %s OR %s IS NULL)
          AND wo.status NOT IN ('CLOSED', 'CANCELLED')
        ORDER BY wo.wo_id
    """, (building, area, area))

    open_wos = cur.fetchall()

    if not open_wos:
        conn.close()
        return {"message": "Tidak ada WO open untuk building ini", "closed": 0}

    closed = 0
    now    = datetime.now()
    end_dt = actual_end or now.strftime("%Y-%m-%d %H:%M:%S")

    for wo in open_wos:
        cur.execute("""
            UPDATE work_order
            SET status      = 'CLOSED',
                actual_end  = %s,
                closed_at   = NOW(),
                assigned_to = %s,
                updated_at  = NOW()
            WHERE wo_id = %s
        """, (end_dt, assigned_to, wo["wo_id"]))
        closed += 1

    conn.commit()
    conn.close()
    return {
        "message" : f"{closed} WO berhasil di-close untuk building '{building}'",
        "closed"  : closed,
        "building": building
    }

@app.get("/buildings")
def list_buildings(area: Optional[str] = Query(None)):
    """List semua building dengan jumlah equipment"""
    conn = get_db()
    cur  = conn.cursor()

    where  = ["e.is_active = TRUE"]
    params = []
    if area:
        where.append("e.area = %s")
        params.append(area)

    cur.execute(f"""
        SELECT e.area, e.building, COUNT(*) AS total_eq,
               COUNT(DISTINCT wo.wo_id) FILTER (WHERE wo.status NOT IN ('CLOSED','CANCELLED') AND wo.status IS NOT NULL) AS open_wo,
               COUNT(DISTINCT wo.wo_id) FILTER (WHERE wo.status = 'CLOSED') AS closed_wo
        FROM equipment e
        LEFT JOIN work_order wo ON wo.eq_id = e.eq_id
        WHERE {' AND '.join(where)}
        GROUP BY e.area, e.building
        ORDER BY e.area, e.building
    """, params)

    rows = cur.fetchall()
    conn.close()
    return {"buildings": rows, "total": len(rows)}

@app.delete("/buildings")
def delete_building(
    building: str = Query(..., description="Nama building yang akan dihapus"),
    area    : str = Query(..., description="Smelter / Desalination / PMR"),
    force   : bool = Query(False, description="Hapus paksa meski ada WO aktif")
):
    """Hapus building beserta semua equipment, maintenance plan, WO, dan notification di dalamnya"""
    conn = get_db()
    cur  = conn.cursor()

    cur.execute("""
        SELECT eq_id, tag_id FROM equipment
        WHERE building = %s AND area = %s
    """, (building, area))
    eqs = cur.fetchall()

    if not eqs:
        conn.close()
        raise HTTPException(404, f"Tidak ada equipment di building '{building}' ({area})")

    eq_ids = [e["eq_id"] for e in eqs]

    cur.execute("""
        SELECT wo_number FROM work_order
        WHERE eq_id = ANY(%s) AND status NOT IN ('CLOSED', 'CANCELLED')
    """, (eq_ids,))
    active_wos = cur.fetchall()

    if active_wos and not force:
        wo_list = [w["wo_number"] for w in active_wos]
        conn.close()
        raise HTTPException(400,
            f"Tidak bisa hapus — ada {len(wo_list)} WO aktif (mis: {', '.join(wo_list[:5])}). "
            f"Gunakan force=true untuk hapus paksa beserta WO-nya."
        )

    cur.execute("SELECT wo_id FROM work_order WHERE eq_id = ANY(%s)", (eq_ids,))
    wo_ids = [w["wo_id"] for w in cur.fetchall()]

    if wo_ids:
        cur.execute("DELETE FROM wo_task_completion WHERE wo_id = ANY(%s)", (wo_ids,))
    cur.execute("DELETE FROM notification WHERE eq_id = ANY(%s)", (eq_ids,))
    cur.execute("DELETE FROM work_order WHERE eq_id = ANY(%s)", (eq_ids,))
    cur.execute("DELETE FROM maintenance_plan WHERE eq_id = ANY(%s)", (eq_ids,))
    cur.execute("DELETE FROM equipment WHERE eq_id = ANY(%s)", (eq_ids,))

    conn.commit()
    conn.close()
    return {
        "message"           : f"Building '{building}' ({area}) berhasil dihapus — {len(eq_ids)} equipment terhapus",
        "deleted_equipment" : len(eq_ids),
        "building"          : building,
        "area"              : area
    }

@app.patch("/buildings/rename")
def rename_building(body: BuildingRename):
    """Pindahkan semua equipment dari satu nama building ke nama building lain (dalam area yang sama)"""
    conn = get_db()
    cur  = conn.cursor()

    new_name = body.new_name.strip()
    if not new_name:
        conn.close()
        raise HTTPException(400, "new_name tidak boleh kosong")

    cur.execute("""
        SELECT COUNT(*) AS c FROM equipment
        WHERE building = %s AND area = %s
    """, (body.old_name, body.area))
    count = cur.fetchone()["c"]

    if count == 0:
        conn.close()
        raise HTTPException(404, f"Tidak ada equipment di building '{body.old_name}' ({body.area})")

    cur.execute("""
        UPDATE equipment SET building = %s
        WHERE building = %s AND area = %s
    """, (new_name, body.old_name, body.area))

    conn.commit()
    conn.close()
    return {
        "message" : f"{count} equipment dipindah dari '{body.old_name}' ke '{new_name}'",
        "updated" : count,
        "area"    : body.area
    }
