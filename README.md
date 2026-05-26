# CVision — Análisis Inteligente de CVs con IA

App web construida con **FastAPI + Google Gemini** para analizar, extraer datos y rankear candidatos a partir de CVs en PDF.

---

## ✨ Funcionalidades

- 📄 **Subida de múltiples CVs** (PDF o TXT, hasta 10 simultáneos)
- 🤖 **Extracción automática con IA** de: nombre, email, teléfono, ubicación, educación, experiencia, habilidades técnicas y blandas, idiomas
- 📊 **Puntuación global** del CV (0–100) con breakdown por dimensiones
- 🎯 **Comparación contra oferta de trabajo**: match score, habilidades faltantes, razones de ajuste
- 🏆 **Ranking automático** de candidatos por score o match
- 💡 **Fortalezas y áreas de mejora** por candidato

---

## 🗂 Estructura del proyecto

```
cv-analyzer/
├── main.py              # Backend FastAPI
├── templates/
│   └── index.html       # Frontend (HTML/CSS/JS)
├── static/              # Archivos estáticos
├── uploads/             # CVs subidos (temporal)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## 🚀 Instalación y uso

### 1. Clonar / copiar el proyecto

```bash
cd cv-analyzer
```

### 2. Crear entorno virtual (recomendado)

```bash
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

> **Nota:** Si usas OCR, también necesitas instalar Tesseract:
> - **Windows:** Ejecuta `install_tesseract.bat` o descarga desde https://github.com/UB-Mannheim/tesseract/wiki
> - **Mac:** `brew install tesseract`
> - **Linux:** `apt-get install tesseract-ocr`

### 4. Configurar Google Gemini API key

Copia el archivo de ejemplo y agrega tu key:

```bash
copy .env.example .env
```

Edita `.env`:

```
GOOGLE_API_KEY=your_api_key_here
```

> Obtén tu **gratuita** API key en: https://aistudio.google.com/app/apikey

### 5. Iniciar el servidor

```bash
uvicorn main:app --reload --port 8000
```

### 6. Abrir la app

Visita: **http://localhost:8000**

### 7. Despliegue en Azure App Service

1. Crea un App Service en Linux con Python 3.x.
2. En la sección **Configuration > General settings**, selecciona la versión de Python y aplica.
3. En **Configuration > Application settings**, agrega:

   - `GOOGLE_API_KEY` = tu_api_key_de_Google_Gemini

4. En **Configuration > General settings** o en el portal de Azure, establece el comando de inicio en:

```bash
bash startup.sh
```

5. Despliega el repositorio. Azure instalará `requirements.txt` automáticamente.

> No olvides usar App Settings de Azure para la clave `GOOGLE_API_KEY` en lugar de subir `.env` al repositorio.

---

## 🔌 API Endpoints

### `POST /api/analyze`

Analiza uno o más CVs.

**Form data:**
| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `cvs` | File(s) | ✅ | PDF o TXT (máx. 10) |
| `job_description` | string | ❌ | Descripción del cargo para comparar |

**Respuesta:**
```json
{
  "success": true,
  "total": 2,
  "has_job_description": true,
  "results": [
    {
      "rank": 1,
      "candidate_name": "María García",
      "overall_score": 87,
      "match_score": 92,
      "skills": { "technical": [...], "soft": [...], "languages": [...] },
      "experience": [...],
      "education": [...],
      "strengths": [...],
      "missing_skills": [...],
      ...
    }
  ],
  "errors": []
}
```

### `POST /auth/register`

Registrar un nuevo usuario.

**Form data:**
| Campo | Tipo | Requerido |
|-------|------|-----------|
| `email` | string | ✅ |
| `password` | string | ✅ |
| `full_name` | string | ❌ |

### `POST /auth/login`

Login y obtener JWT token.

**Form data:**
| Campo | Tipo |
|-------|------|
| `email` | string |
| `password` | string |

**Respuesta:**
```json
{
  "success": true,
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "user": { "id": 1, "email": "user@mail.com", "full_name": "John Doe" }
}
```

### `GET /api/export/excel/{analysis_id}`

Descargar análisis individual en Excel.

### `POST /api/export/excel`

Descargar múltiples análisis en Excel.

**Form data:**
| Campo | Tipo |
|-------|------|
| `result_ids` | List[int] |

### `GET /api/export/pdf/{analysis_id}`

Descargar análisis individual en PDF.

### `POST /api/export/pdf`

Descargar múltiples análisis en PDF.

**Form data:**
| Campo | Tipo |
|-------|------|
| `result_ids` | List[int] |

### `POST /api/analyze-with-ocr`

Analiza CVs forzando el uso de OCR para todos los PDFs (útil para CVs escaneados).

**Form data:**
| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `cvs` | File(s) | ✅ | PDFs o TXT (máx. 10) |
| `job_description` | string | ❌ | Descripción del cargo para comparar |

**Nota:** Requiere que Tesseract esté instalado en el sistema.

---

## ⚙️ Variables de entorno

| Variable | Descripción |
|----------|-------------|
| `GOOGLE_API_KEY` | API key de Google Gemini |
| `SECRET_KEY` | Clave secreta para JWT (cambiar en producción) |
| `GEMINI_MODEL` | Modelo de Gemini a usar (default: `gemini-2.5-flash`) |

---

## 🛠 Stack tecnológico

| Componente | Tecnología |
|------------|------------|
| Backend | FastAPI + Uvicorn |
| IA | Google Gemini 1.5 Flash |
| Extracción PDF | pdfplumber |
| Frontend | HTML5 + CSS3 + Vanilla JS |
| Fuentes | Playfair Display, DM Mono, Syne |

---

## 📌 Notas

- Los CVs se procesan en memoria; no se almacenan permanentemente.
- El análisis puede tardar 5–15 segundos por CV dependiendo del tamaño.
- Se recomienda CVs de buena calidad (texto seleccionable, no escaneados).
- **OCR automático:** Si un PDF parece escaneado, se intenta usar OCR automáticamente.
- **OCR manual:** Usa `/api/analyze-with-ocr` para forzar OCR en todos los PDFs.
- Para PDFs escaneados, instala Tesseract OCR en el sistema operativo.

---

## 🔮 Mejoras implementadas y futuras

### ✅ Ya implementadas

- **[x] Exportar resultados a Excel/PDF** — Endpoints `/api/export/excel` y `/api/export/pdf`
- **[x] Historial de análisis con base de datos** — SQLite con tabla `analysis_history`, endpoint `/api/history`
- **[x] Autenticación de usuarios** — JWT con endpoints `/auth/register` y `/auth/login`, tabla `users`

### 🚀 Mejoras futuras

#### OCR para CVs escaneados
- **[x] Implementado:** Detección automática de PDFs escaneados y uso de OCR
- **[x] Endpoint:** `POST /api/analyze-with-ocr` para forzar OCR en todos los PDFs
- **Dependencias:** `pytesseract`, Tesseract OCR instalado en el sistema
- **Instalación Tesseract:**
  - **Windows:** Descargar desde https://github.com/UB-Mannheim/tesseract/wiki
  - **Mac:** `brew install tesseract`
  - **Linux:** `apt-get install tesseract-ocr`

#### Modo de entrevista
- [ ] Generar preguntas técnicas personalizadas basadas en el CV.
- [ ] Tabla `interview_sessions` para guardar sesiones.
- [ ] Evaluación automática de respuestas con Gemini.

#### Dashboard y Analytics
- **[x] Implementado:** Dashboard completo con métricas, gráficos y análisis recientes
- **[x] Endpoint:** `GET /api/dashboard` retorna todas las métricas
- **Gráficos:** Distribución de scores (dona) y análisis por mes (línea) con Chart.js
- **Métricas:** Total análisis, candidatos únicos, score promedio, análisis con oferta trabajo

#### Mejoras de seguridad y performance
- [ ] Rate limiting con `slowapi`.
- [ ] Caché de resultados.
- [ ] Soporte para múltiples idiomas (i18n).
- [ ] RBAC (role-based access control) para admins.

#### Integraciones
- [ ] Exportar a Google Drive / OneDrive.
- [ ] Envío de resultados por email.
- [ ] Webhook para notificaciones externas.

---
# Análisis
