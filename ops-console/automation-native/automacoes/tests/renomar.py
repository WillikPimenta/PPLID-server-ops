
import pandas as pd

# Ler o parquet
df = pd.read_parquet(r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Detalhado de produtividade\DETALHADO_FY_27\brflow-prod-tratado_20260613.parquet")

# Renomear coluna
df = df.rename(columns={"desMatricula": "matricula"})

# Salvar de volta
df.to_parquet(r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP\Planejamento - IDF - Bases\Detalhado de produtividade\DETALHADO_FY_27\brflow-prod-tratado_20260613.parquet", index=False)
