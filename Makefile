.PHONY: install data api app test demo lint web web-demo serve

PYTHONPATH := src
DEP ?= 44

install:
	python -m pip install -r requirements.txt

data:
	PYTHONPATH=$(PYTHONPATH) python -m medaccess.data --dep $(DEP) --refresh

api:
	PYTHONPATH=$(PYTHONPATH) python -m uvicorn medaccess.api:app --reload --port 8000

app:
	PYTHONPATH=$(PYTHONPATH) python -m streamlit run app/streamlit_app.py

demo:
	FORCE_SYNTHETIC=true PYTHONPATH=$(PYTHONPATH) python -m streamlit run app/streamlit_app.py

test:
	FORCE_SYNTHETIC=true PYTHONPATH=$(PYTHONPATH) python -m pytest -q

lint:
	ruff check src app tests

# --- Appli web installable (PWA) ---
web:            ## exporte les données réelles (après make data) vers web/data
	PYTHONPATH=$(PYTHONPATH) python -m medaccess.export --dep $(DEP)

web-demo:       ## exporte la démo (médecins simulés)
	FORCE_SYNTHETIC=true PYTHONPATH=$(PYTHONPATH) python -m medaccess.export

serve:          ## ouvre l'appli web sur http://localhost:8080
	python -m http.server 8080 --directory web
