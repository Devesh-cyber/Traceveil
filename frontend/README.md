# Traceveil frontend

React + TypeScript + Vite.

    npm install
    npm run dev        # http://localhost:5173  (forwards /api to the backend on port 8000)
    npm run build      # type-checks, then builds into dist/

The backend (`uvicorn app.main:app --port 8000`) and the worker (`python -m app.worker.main`) must both be running.
