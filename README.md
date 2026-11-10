# CriterIA

Chatbot web para comprender y analizar dilemas éticos sobre inteligencia artificial, comunicación y periodismo. Responde **solo con los PDF del corpus**, cita la página de cada afirmación y dice «no tengo evidencia» cuando no la encuentra. Proyecto del Reto 2026, UTPL.

| Parte | Tecnología |
| --- | --- |
| Web | Django 6.1 (plantillas, JavaScript sin frameworks) |
| Modelo de chat | `qwen3.5:4b` en Ollama, 100 % local |
| Búsqueda en los PDF | `bge-m3` (semántica) + BM25 (léxica) |
| Voz a texto | `faster-whisper`, local |
| Imagen y audio | Gemini, opcional |

## Instalación

Requisitos: Python 3.13, [Ollama](https://ollama.com) y `ffmpeg`. El modelo de chat necesita unos 4 GB de VRAM.

```bash
ollama pull qwen3.5:4b
ollama pull bge-m3

python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

python manage.py migrate
python manage.py createsuperuser   # para entrar a /admin/
```

## Cargar los PDF

1. Copia los PDF en `corpus_pdfs/`. No están en el repositorio por derechos de autor y tamaño: pídelos al equipo.
2. `corpus_pdfs/metadata.csv` ya trae título, autor y año de los PDF actuales. Para un PDF nuevo, agrega una fila con su nombre de archivo exacto. Sin autor y año, las citas salen incompletas y la interfaz lo advierte.
3. Indexa:

```bash
python manage.py ingest_pdfs
```

Puedes repetir el comando: solo procesa los archivos nuevos y actualiza los metadatos. Con `--reindex` reprocesa todo. También se pueden subir PDF desde `/admin/`.

Para PDF escaneados hace falta OCR en español y portugués. En Fedora: `sudo dnf install tesseract-langpack-spa tesseract-langpack-por`.

## Ejecutar

```bash
python manage.py runserver
```

Abre <http://127.0.0.1:8000>. La guía para usuarios está en `/guia/`.

La primera vez que se usa el micrófono se descargan unos 500 MB del modelo de voz. El navegador solo permite el micrófono en `localhost` o por HTTPS.

## Imagen y audio con Gemini (opcional)

Añade `GEMINI_API_KEY` en `.env`. Las imágenes no tienen capa gratuita: requieren facturación activa en Google AI Studio. El audio sí tiene capa gratuita.

Gemini solo convierte en imagen o audio una respuesta ya fundamentada, y no tiene acceso a búsquedas. Hay un límite diario por usuario, configurable en `.env`.

## Cómo funciona una consulta

1. Detecta el idioma (español, inglés o portugués).
2. Clasifica la intención: consulta, ambigua, fuera de dominio o sobre el asistente.
3. Busca en los PDF y mide la similitud real.
4. Si no supera `RAG_MIN_SIMILARITY`, responde con una limitación sin llamar al modelo.
5. Si la supera, el modelo redacta usando solo esos fragmentos.
6. Se validan las citas: una respuesta sin citas válidas se convierte en limitación.
7. Si el usuario tiene cuenta, se guarda en su historial. Del invitado no se guarda nada.

El código está en [`chat/pipeline.py`](chat/pipeline.py).

## Ajustar el umbral de evidencia

`RAG_MIN_SIMILARITY` (por defecto 0.50) debe calibrarse con el corpus real. Si rechaza preguntas que sí están en los PDF, bájalo. Si intenta responder sobre lo que no está, súbelo.

## Pruebas

```bash
python manage.py test
```

## Evaluar la calidad de las respuestas

```bash
python manage.py evaluar                  # una pasada por las preguntas de control
python manage.py evaluar --repeticiones 3 # comprueba que las respuestas sean estables
```

Usa el modelo real y las preguntas de `evaluacion/preguntas.csv`. En el grupo `dentro` la respuesta está en el corpus: se espera una respuesta con citas en el idioma de la pregunta. En el grupo `fuera` no lo está: se espera que el asistente diga que no tiene evidencia. Repite la evaluación al añadir PDF o cambiar de modelo, y agrega preguntas propias al CSV.

## Estructura

```
criteria/   configuración de Django
accounts/   registro, inicio de sesión y perfil
corpus/     ingesta de PDF, búsqueda, citas y visor de páginas
chat/       flujo conversacional, historial, voz, exportación y Gemini
locale/     traducciones de la interfaz (en, pt)
templates/  páginas
static/     estilos, JavaScript y librerías locales
```

Licencias: PyMuPDF se distribuye bajo AGPL; si el proyecto se publica como servicio, el código debe ser abierto o hay que adquirir su licencia comercial.
