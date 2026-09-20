#!/usr/bin/env python3
"""Command line helpers.

  python manage.py init                 create the database, seed schools, import data/sample_feedback.csv
  python manage.py import FILE.csv      import another export of the feedback form
  python manage.py sentiment [SPEC]     run AI sentiment (SPEC: hf or hf:<model-id>; default from SENTIMENT_MODEL)
  python manage.py run                  start the dashboard on http://127.0.0.1:8000
"""
import sys

from app import config, db, importer


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "run":
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
        return
    conn = db.connect()
    db.init_db(conn)
    if cmd == "init":
        print(f"Database: {config.DB_PATH}")
        if config.SAMPLE_CSV.exists():
            print(importer.import_csv_file(conn, config.SAMPLE_CSV))
    elif cmd == "import" and len(argv) > 2:
        print(importer.import_csv_file(conn, argv[2]))
    elif cmd == "sentiment":
        from app.sentiment import get_model
        from app.sentiment.service import run_analysis
        model = get_model(argv[2] if len(argv) > 2 else None)
        if model is None:
            sys.exit("No model configured. Use: python manage.py sentiment hf")
        print(run_analysis(conn, model))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
