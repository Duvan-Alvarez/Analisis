import os
import asyncio
import io
import json
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime

import pdfplumber
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker, Session
import google.generativeai as genai
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from export_utils import export_to_excel, export_to_pdf
from auth import hash_password, verify_password, create_access_token, verify_token

# OCR imports (opcional)
try:
    import pytesseract
    from PIL import Image
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False

if not os.getenv("GOOGLE_API_KEY"):
    print("WARNING: GOOGLE_API_KEY no está configurada. Crea un archivo .env con tu clave o exporta la variable de entorno.")

app = FastAPI(title="CV Analyzer AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", DATA_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Modelo Gemini configurado desde env o por defecto.
# gemini-flash-latest funciona en este entorno y es una opción estable.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

# Limitar concurrencia a 3 análisis simultáneos para evitar saturar API
MAX_CONCURRENT_ANALYSES = 3
analysis_semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSES)

# --- Configuración de la Base de Datos ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'cv_analysis_history.db'}")
SQLALCHEMY_CONNECT_ARGS = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL, connect_args=SQLALCHEMY_CONNECT_ARGS
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Modelo de la tabla para el historial de análisis
class AnalysisHistory(Base):
    __tablename__ = "analysis_history"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.now)
    filename = Column(String, index=True)
    candidate_name = Column(String, nullable=True)
    overall_score = Column(Integer, nullable=True)
    match_score = Column(Integer, nullable=True)
    job_description_used = Column(Boolean, default=False)
    job_description_text = Column(Text, nullable=True)
    full_result_json = Column(Text) # Para guardar el JSON completo del análisis

    def __repr__(self):
        return f"<AnalysisHistory(id={self.id}, filename='{self.filename}', candidate_name='{self.candidate_name}')>"

# Modelo de la tabla para usuarios
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}')>"

# Crear las tablas en la base de datos al iniciar la aplicación
@app.on_event("startup")
async def startup_event():
    Base.metadata.create_all(bind=engine)

# Dependencia para obtener la sesión de la base de datos
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Configuración de la IA de Google Gemini ---
try:
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
except Exception as e:
    print(f"Error al configurar la API de Google: {e}")

GEMINI_MODEL_INSTANCE = None

def get_gemini_model(model_name: Optional[str] = None):
    global GEMINI_MODEL_INSTANCE
    if model_name is None:
        model_name = GEMINI_MODEL
    if GEMINI_MODEL_INSTANCE is None or GEMINI_MODEL_INSTANCE.model_name != model_name:
        GEMINI_MODEL_INSTANCE = genai.GenerativeModel(model_name)
    return GEMINI_MODEL_INSTANCE


def extract_text_from_pdf(file_bytes: bytes) -> Tuple[str, bool]:
    """Extrae texto de un PDF usando pdfplumber, con OCR opcional para PDFs escaneados."""
    text = ""
    used_ocr = False

    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                elif OCR_AVAILABLE:
                    # Si no hay texto extraíble, intentar OCR
                    try:
                        image = page.to_image(resolution=200).original
                        ocr_text = pytesseract.image_to_string(image, lang='spa+eng')
                        if ocr_text.strip():
                            text += ocr_text + "\n"
                            used_ocr = True
                    except Exception as e:
                        print(f"Error en OCR: {e}")
                        continue
    except Exception as e:
        print(f"Error procesando PDF: {e}")

    return text.strip(), used_ocr


def is_scanned_pdf(file_bytes: bytes) -> bool:
    """Detecta si un PDF es principalmente escaneado (sin texto extraíble)."""
    if not OCR_AVAILABLE:
        return False
    
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            total_pages = len(pdf.pages)
            text_pages = 0
            
            for page in pdf.pages[:min(3, total_pages)]:  # Revisar primeras 3 páginas
                page_text = page.extract_text()
                if page_text and len(page_text.strip()) > 50:  # Texto significativo
                    text_pages += 1
            
            # Si menos del 50% de las páginas tienen texto, considerar escaneado
            return text_pages / min(3, total_pages) < 0.5
    except Exception:
        return False


def analyze_cv_with_ai(cv_text: str, job_description: Optional[str] = None) -> dict:
    """Analiza un CV usando Gemini y retorna datos estructurados de forma rápida."""

    job_context = ""
    if job_description:
        job_context = f"\nOferta de trabajo para comparación:\n{job_description[:1000]}"

    # Prompt comprimido para análisis rápido
    prompt = f"""Eres un experto en análisis de CVs. Analiza el siguiente CV y retorna SOLO un JSON válido, sin markdown ni explicaciones.

CV:
{cv_text[:6000]}{job_context}

Retorna exactamente este JSON:
{{
  "candidate_name": "nombre o null",
  "email": "email o null",
  "phone": "teléfono o null",
  "location": "ciudad o null",
  "summary": "2-3 frases",
  "years_of_experience": 0,
  "education": [{{"degree": "título", "institution": "institución", "year": "año"}}],
  "skills": {{"technical": ["skill1", "skill2"], "soft": ["blanda1"], "languages": ["idioma1"]}},
  "experience": [{{"title": "cargo", "company": "empresa", "duration": "2020-2022", "highlights": ["logro1"]}}],
  "strengths": ["fortaleza1", "fortaleza2", "fortaleza3"],
  "areas_for_improvement": ["área1", "área2"],
  "overall_score": 75,
  "score_breakdown": {{"experience": 75, "education": 80, "skills": 70, "presentation": 75}},
  "match_score": null,
  "match_reasons": [],
  "missing_skills": []
}}"""

    model_name = GEMINI_MODEL
    model = get_gemini_model(model_name)
    
    try:
        response = model.generate_content(prompt)
    except Exception as e:
        if model_name != DEFAULT_GEMINI_MODEL:
            print(f"Modelo {model_name} falló: {e}. Reintentando con {DEFAULT_GEMINI_MODEL}.")
            model = get_gemini_model(DEFAULT_GEMINI_MODEL)
            response = model.generate_content(prompt)
        else:
            print(f"Error en generación de contenido con {model_name}: {e}")
            raise

    raw = response.text.strip()

    # Limpiar backticks si los hay
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:].strip()
        raw = raw.rstrip("```").strip()

    try:
        result = json.loads(raw)
        return result
    except json.JSONDecodeError as e:
        print(f"Error al parsear JSON: {e}")
        print(f"Raw response: {raw[:400]}")
        raise ValueError(f"La IA no retornó JSON válido: {str(e)}")

# --- Datos de ejemplo para vacantes ---
VACANTES = [
    {
        "id": 17,
        "titulo": "Ejecutivo de ventas Bogota",
        "area": "Comercial",
        "contrato": "Indefinido",
        "fecha": "06/04/2026",
        "descripcion": "Cargo: Ejecutivo de ventas Bogota\nÁrea: Comercial | Contrato: Indefinido\n\nDESCRIPCIÓN:\nLiderar negociaciones del canal institucional y clientes estratégicos, generando relaciones sólidas y de confianza.\n\nFUNCIONES:\nGestionar y fortalecer el canal de ventas, asegurando el cumplimiento del presupuesto mediante planificación estratégica y toma de decisiones comerciales.\n\nREQUISITOS:\n- Profesional en Mercadeo, Ventas, Administración o afines.\n- Experiencia en cargos comerciales, canal institucional o tradicional.\n- Conocimiento en procesos de venta, negociación y seguimiento al cliente.\n- Dominio de herramientas ofimáticas."
    },
    {
        "id": 16,
        "titulo": "Auxiliar de calidad",
        "area": "Calidad",
        "contrato": "Temporal",
        "fecha": "12/03/2026",
        "descripcion": "Cargo: Auxiliar de calidad\nÁrea: Calidad | Contrato: Temporal\n\nDESCRIPCIÓN:\nGarantizar el cumplimiento de los estándares de calidad e inocuidad durante el proceso de producción y en el producto terminado.\n\nFUNCIONES:\n- Seguimiento a etapas de elaboración de productos.\n- Monitoreo de pesos netos y condiciones de proceso.\n- Análisis fisicoquímicos y sensoriales.\n- Muestreo microbiológico.\n- Diligenciamiento de registros y certificados de calidad.\n\nREQUISITOS:\n- Técnico o tecnólogo en calidad, alimentos, industrial o afines.\n- Conocimientos en HACCP, BPM y sistemas de gestión de calidad.\n- Metrología básica y herramientas ofimáticas.\n- 1 año de experiencia."
    },
    {
        "id": 14,
        "titulo": "Auxiliar logístico",
        "area": "Logística",
        "contrato": "Por obra",
        "fecha": "17/02/2026",
        "descripcion": "Cargo: Auxiliar logístico\nÁrea: Logística | Contrato: Por obra\n\nDESCRIPCIÓN:\nApoyo en procesos de recepción, almacenamiento y control de inventarios, asegurando el adecuado manejo del producto terminado y cumplimiento de condiciones de orden, aseo e higiene en bodega.\n\nFUNCIONES:\nGarantizar la correcta rotación de inventarios y el adecuado control de los productos almacenados.\n\nREQUISITOS:\n- 6 meses de experiencia en el área.\n- Bachiller."
    },
    {
        "id": 3,
        "titulo": "Auxiliar de producción",
        "area": "Producción",
        "contrato": "Por obra",
        "fecha": "20/10/2025",
        "descripcion": "Cargo: Auxiliar de producción\nÁrea: Producción | Contrato: Por obra (6 meses, posibilidad de vinculación)\nUbicación: La Estrella\n\nDESCRIPCIÓN:\nRealizar procesos operativos de tratamiento térmico, emulsiones, envasado y embalaje en planta de producción bajo estándares de calidad, garantizando la calidad e inocuidad del empaque y producto.\n\nBENEFICIOS:\n- Salario + auxilio de transporte\n- Prestaciones de ley\n- Bonos por cumplimiento de metas\n- Turnos rotativos lunes a sábado\n\nREQUISITOS:\n- Experiencia mínima de 6 meses en plantas de producción."
    }
]


@app.get("/api/vacantes")
async def get_vacantes():
    return JSONResponse({"vacantes": VACANTES})


@app.get("/api/health")
async def healthcheck():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/analyze")
async def analyze_cvs(
    cvs: List[UploadFile] = File(...),
    job_description: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Analiza uno o varios CVs y los rankea."""
    if not cvs:
        raise HTTPException(status_code=400, detail="Debes subir al menos un CV.")
    
    if len(cvs) > 10:
        raise HTTPException(status_code=400, detail="Máximo 10 CVs por análisis.")

    errors = []
    
    async def process_file(cv_file: UploadFile):
        try:
            content = await cv_file.read()
            text = ""
            
            if cv_file.filename.lower().endswith(".pdf"):
                text, used_ocr = await asyncio.to_thread(extract_text_from_pdf, content)
                
                if not text:
                    if OCR_AVAILABLE:
                        return {"error": "No se pudo extraer texto del PDF. Puede ser escaneado o no legible.", "filename": cv_file.filename}
                    else:
                        return {"error": "PDF no contiene texto y OCR no está disponible. Instala Tesseract.", "filename": cv_file.filename}
            else:
                text = content.decode("utf-8", errors="ignore")
            
            if not text or len(text) < 50:
                return {"error": "No se pudo extraer suficiente texto del archivo.", "filename": cv_file.filename}
            
            analysis = await asyncio.to_thread(analyze_cv_with_ai, text, job_description)
            analysis["filename"] = cv_file.filename
            analysis["processed_with_ocr"] = used_ocr if cv_file.filename.lower().endswith(".pdf") else False
            return analysis
        except json.JSONDecodeError:
            return {"error": "Error al parsear la respuesta de IA.", "filename": cv_file.filename}
        except Exception as e:
            return {"error": str(e), "filename": cv_file.filename}

    async def process_file_limited(cv_file: UploadFile):
        async with analysis_semaphore:
            return await process_file(cv_file)
    
    tasks = [process_file_limited(cv) for cv in cvs]
    task_results = await asyncio.gather(*tasks)

    results = []
    for res in task_results:
        if "error" in res:
            errors.append(res)
        else:
            results.append(res)

    # Rankear por match_score si hay oferta, sino por overall_score
    sort_key = "match_score" if job_description else "overall_score"
    results.sort(key=lambda x: x.get(sort_key) or x.get("overall_score", 0), reverse=True)

    # Agregar posición de ranking
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # --- Guardar resultados en la base de datos ---
    try:
        for res in results:
            history_entry = AnalysisHistory(
                filename=res.get("filename", "unknown"),
                candidate_name=res.get("candidate_name", "N/A"),
                overall_score=res.get("overall_score"),
                match_score=res.get("match_score"),
                job_description_used=bool(job_description),
                job_description_text=job_description,
                full_result_json=json.dumps(res)
            )
            db.add(history_entry)
        db.commit()
    except Exception as e:
        # Manejo de errores al guardar en DB, no debe bloquear la respuesta principal
        print(f"Error al guardar en la base de datos: {e}")
        errors.append({"type": "database_error", "message": "No se pudo guardar el historial en la base de datos."})

    return JSONResponse({
        "success": True,
        "total": len(results),
        "has_job_description": bool(job_description),
        "results": results,
        "errors": errors
    })

@app.get("/api/history")
async def get_analysis_history(db: Session = Depends(get_db)):
    """Retorna el historial de análisis de CVs."""
    history_entries = db.query(AnalysisHistory).order_by(AnalysisHistory.timestamp.desc()).all()
    
    # Convertir los objetos de SQLAlchemy a diccionarios para la respuesta JSON
    formatted_history = []
    for entry in history_entries:
        full_result = json.loads(entry.full_result_json)
        formatted_history.append({
            "id": entry.id,
            "timestamp": entry.timestamp.isoformat(),
            "filename": entry.filename,
            "candidate_name": entry.candidate_name,
            "overall_score": entry.overall_score,
            "match_score": entry.match_score,
            "job_description_used": entry.job_description_used,
            "job_description_text": entry.job_description_text,
            "full_result": full_result # El JSON completo del análisis
        })
    return JSONResponse({"history": formatted_history})


# ============== ENDPOINT PARA ANÁLISIS CON OCR ==============

@app.post("/api/analyze-with-ocr")
async def analyze_cvs_with_ocr(
    cvs: List[UploadFile] = File(...),
    job_description: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Analiza CVs forzando el uso de OCR para todos los PDFs."""
    if not cvs:
        raise HTTPException(status_code=400, detail="Debes subir al menos un CV.")
    
    if len(cvs) > 10:
        raise HTTPException(status_code=400, detail="Máximo 10 CVs por análisis.")
    
    if not OCR_AVAILABLE:
        raise HTTPException(status_code=503, detail="OCR no está disponible. Instala pytesseract y Tesseract.")
    
    errors = []
    
    async def process_file_with_ocr(cv_file: UploadFile):
        try:
            content = await cv_file.read()
            text = ""
            
            if cv_file.filename.lower().endswith(".pdf"):
                text, _ = await asyncio.to_thread(extract_text_from_pdf, content)
            else:
                text = content.decode("utf-8", errors="ignore")
            
            if not text or len(text) < 50:
                return {"error": "No se pudo extraer suficiente texto del archivo.", "filename": cv_file.filename}
            
            analysis = await asyncio.to_thread(analyze_cv_with_ai, text, job_description)
            analysis["filename"] = cv_file.filename
            analysis["processed_with_ocr"] = True
            return analysis
        except json.JSONDecodeError:
            return {"error": "Error al parsear la respuesta de IA.", "filename": cv_file.filename}
        except Exception as e:
            return {"error": str(e), "filename": cv_file.filename}

    async def process_file_ocr_limited(cv_file: UploadFile):
        async with analysis_semaphore:
            return await process_file_with_ocr(cv_file)
    
    tasks = [process_file_ocr_limited(cv) for cv in cvs]
    task_results = await asyncio.gather(*tasks)

    results = []
    for res in task_results:
        if "error" in res:
            errors.append(res)
        else:
            results.append(res)

    # Rankear por match_score si hay oferta, sino por overall_score
    sort_key = "match_score" if job_description else "overall_score"
    results.sort(key=lambda x: x.get(sort_key) or x.get("overall_score", 0), reverse=True)

    # Agregar posición de ranking
    for i, r in enumerate(results):
        r["rank"] = i + 1

    # --- Guardar resultados en la base de datos ---
    try:
        for res in results:
            history_entry = AnalysisHistory(
                filename=res.get("filename", "unknown"),
                candidate_name=res.get("candidate_name", "N/A"),
                overall_score=res.get("overall_score"),
                match_score=res.get("match_score"),
                job_description_used=bool(job_description),
                job_description_text=job_description,
                full_result_json=json.dumps(res)
            )
            db.add(history_entry)
        db.commit()
    except Exception as e:
        print(f"Error al guardar en la base de datos: {e}")
        errors.append({"type": "database_error", "message": "No se pudo guardar el historial en la base de datos."})

    return JSONResponse({
        "success": True,
        "total": len(results),
        "has_job_description": bool(job_description),
        "results": results,
        "errors": errors,
        "ocr_used": True
    })


# ============== ENDPOINT PARA DASHBOARD ==============

@app.get("/api/dashboard")
async def get_dashboard_metrics(db: Session = Depends(get_db)):
    """Retorna métricas y estadísticas para el dashboard."""
    try:
        # Obtener todos los análisis
        all_entries = db.query(AnalysisHistory).all()
        
        if not all_entries:
            return JSONResponse({
                "total_analyses": 0,
                "total_candidates": 0,
                "avg_score": 0,
                "score_distribution": {},
                "recent_analyses": [],
                "top_skills": [],
                "analyses_by_month": [],
                "job_descriptions_used": 0
            })
        
        # Calcular métricas básicas
        total_analyses = len(all_entries)
        total_candidates = len(set(entry.candidate_name for entry in all_entries if entry.candidate_name != "N/A"))
        
        scores = [entry.overall_score for entry in all_entries if entry.overall_score is not None]
        avg_score = sum(scores) / len(scores) if scores else 0
        
        # Distribución de scores
        score_ranges = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
        for score in scores:
            if score <= 20:
                score_ranges["0-20"] += 1
            elif score <= 40:
                score_ranges["21-40"] += 1
            elif score <= 60:
                score_ranges["41-60"] += 1
            elif score <= 80:
                score_ranges["61-80"] += 1
            else:
                score_ranges["81-100"] += 1
        
        # Análisis recientes (últimos 10)
        recent_analyses = []
        for entry in all_entries[-10:]:
            recent_analyses.append({
                "id": entry.id,
                "timestamp": entry.timestamp.isoformat(),
                "filename": entry.filename,
                "candidate_name": entry.candidate_name,
                "overall_score": entry.overall_score,
                "match_score": entry.match_score
            })
        
        # Top skills (extraer de los JSON completos)
        skill_counts = {}
        for entry in all_entries:
            try:
                full_data = json.loads(entry.full_result_json)
                skills = full_data.get("skills", {})
                technical = skills.get("technical", [])
                soft = skills.get("soft", [])
                
                for skill in technical + soft:
                    skill_counts[skill] = skill_counts.get(skill, 0) + 1
            except:
                continue
        
        top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        
        # Análisis por mes
        from collections import defaultdict
        analyses_by_month = defaultdict(int)
        for entry in all_entries:
            month_key = entry.timestamp.strftime("%Y-%m")
            analyses_by_month[month_key] += 1
        
        monthly_data = [{"month": month, "count": count} for month, count in sorted(analyses_by_month.items())]
        
        # Job descriptions used
        job_descriptions_used = sum(1 for entry in all_entries if entry.job_description_used)
        
        return JSONResponse({
            "total_analyses": total_analyses,
            "total_candidates": total_candidates,
            "avg_score": round(avg_score, 1),
            "score_distribution": score_ranges,
            "recent_analyses": recent_analyses,
            "top_skills": [{"skill": skill, "count": count} for skill, count in top_skills],
            "analyses_by_month": monthly_data,
            "job_descriptions_used": job_descriptions_used
        })
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ============== ENDPOINTS DE AUTENTICACIÓN ==============

@app.post("/auth/register")
async def register(email: str = Form(...), password: str = Form(...), full_name: str = Form(None), db: Session = Depends(get_db)):
    """Registrar un nuevo usuario."""
    # Verificar si el usuario ya existe
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="El email ya está registrado.")
    
    # Crear nuevo usuario
    hashed_pwd = hash_password(password)
    new_user = User(email=email, hashed_password=hashed_pwd, full_name=full_name)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return JSONResponse({
        "success": True,
        "message": "Usuario registrado exitosamente.",
        "user": {
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name
        }
    })


@app.post("/auth/login")
async def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    """Login de usuario y obtener JWT token."""
    user = db.query(User).filter(User.email == email).first()
    
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos.")
    
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Usuario desactivado.")
    
    # Crear token JWT
    access_token = create_access_token(data={"sub": user.email})
    
    return JSONResponse({
        "success": True,
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name
        }
    })


# ============== ENDPOINTS DE EXPORTACIÓN ==============

@app.get("/api/export/excel/{analysis_id}")
async def export_analysis_excel(analysis_id: int, db: Session = Depends(get_db)):
    """Exportar un análisis específico a Excel."""
    entry = db.query(AnalysisHistory).filter(AnalysisHistory.id == analysis_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Análisis no encontrado.")
    
    result = json.loads(entry.full_result_json)
    excel_file = export_to_excel([result], entry.job_description_text)
    
    return FileResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"analisis_{entry.candidate_name or 'cv'}_{entry.id}.xlsx"
    )


@app.post("/api/export/excel")
async def export_multiple_excel(result_ids: List[int] = Form(...), db: Session = Depends(get_db)):
    """Exportar múltiples análisis a Excel."""
    entries = db.query(AnalysisHistory).filter(AnalysisHistory.id.in_(result_ids)).all()
    if not entries:
        raise HTTPException(status_code=404, detail="No se encontraron análisis.")
    
    results = [json.loads(entry.full_result_json) for entry in entries]
    excel_file = export_to_excel(results)
    
    return FileResponse(
        excel_file,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="analisis_candidatos.xlsx"
    )


@app.get("/api/export/pdf/{analysis_id}")
async def export_analysis_pdf(analysis_id: int, db: Session = Depends(get_db)):
    """Exportar un análisis específico a PDF."""
    entry = db.query(AnalysisHistory).filter(AnalysisHistory.id == analysis_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Análisis no encontrado.")
    
    result = json.loads(entry.full_result_json)
    pdf_file = export_to_pdf([result], entry.job_description_text)
    
    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename=f"analisis_{entry.candidate_name or 'cv'}_{entry.id}.pdf"
    )


@app.post("/api/export/pdf")
async def export_multiple_pdf(result_ids: List[int] = Form(...), db: Session = Depends(get_db)):
    """Exportar múltiples análisis a PDF."""
    entries = db.query(AnalysisHistory).filter(AnalysisHistory.id.in_(result_ids)).all()
    if not entries:
        raise HTTPException(status_code=404, detail="No se encontraron análisis.")
    
    results = [json.loads(entry.full_result_json) for entry in entries]
    pdf_file = export_to_pdf(results)
    
    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename="analisis_candidatos.pdf"
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
