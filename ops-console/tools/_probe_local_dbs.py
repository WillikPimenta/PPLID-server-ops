import psycopg

with psycopg.connect(
    host="127.0.0.1",
    port=5432,
    dbname="postgres",
    user="postgres",
    password="postgres",
    connect_timeout=4,
) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT datname FROM pg_database WHERE datname LIKE 'pplid%' ORDER BY 1")
        print(",".join(row[0] for row in cur.fetchall()))
