import os
import sys
project_dir = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
sys.path.append(project_dir)

import dotenv
import json
from io import StringIO
import geopandas as gpd

from sqlalchemy import create_engine, Column, Integer, DateTime, UniqueConstraint, exc
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import inspect, MetaData
from sqlalchemy.orm import sessionmaker
from sqlalchemy.types import JSON as typeJSON

from src.data.functions import (tabulate_records, prep_rawdata_tosql,
                               build_df_jams, prep_jams_tosql, build_geo_trechos,
                               get_impacted_trechos, explode_impacted_trechos, prep_jpt_tosql)

project_dir = os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
dotenv_path = os.path.join(project_dir, '.env')
dotenv.load_dotenv(dotenv_path)

#Connection and initial setup
DATABASE = {
    'drivername': os.environ.get("test_db_drivername"),
    'host': os.environ.get("test_db_host"), 
    'port': os.environ.get("test_db_port"),
    'username': os.environ.get("test_db_username"),
    'password': os.environ.get("test_db_password"),
    'database': os.environ.get("test_db_database"),
}

db_url = URL(**DATABASE)
engine = create_engine(db_url)
Base = declarative_base()
meta = MetaData()
meta.bind = engine
meta.reflect()

#Store Mongo Record info
mongo_record = meta.tables["MongoRecord"]
mongo_record.delete().execute()
file = open(project_dir + "/data/raw/waze_rawdata.txt", "r")
json_string = json.load(file)
json_io = StringIO(json_string)
records = json.load(json_io)
raw_data = tabulate_records(records)
rawdata_tosql = prep_rawdata_tosql(raw_data)
rawdata_tosql.to_sql("MongoRecord", con=meta.bind, if_exists="append", index=False)


#Build dataframe and store in PostgreSQL
try:
  df_jams = build_df_jams(raw_data)
except exceptions.NoJamError:
    print("No Jam in the given period")
    sys.exit()
jam = meta.tables["Jam"]
jam.delete().execute()
jams_tosql = prep_jams_tosql(df_jams)
jams_tosql.to_sql("Jam", con=meta.bind, if_exists="append", index=False,
                 dtype={"JamDscCoordinatesLonLat": typeJSON, 
                        "JamDscSegments": typeJSON
                       }
                 )

#Build and store JamPerTrecho
geo_trechos = build_geo_trechos(meta)
#df_jams['impacted_trechos'] = df_jams.apply(lambda x: get_impacted_trechos(x, geo_trechos), axis=1)
jams_per_trecho = gpd.sjoin(df_jams, geo_trechos, how="inner", op="contains")
#jams_per_trecho = explode_impacted_trechos(df_jams)
jpt_tosql = prep_jpt_tosql(jams_per_trecho) 
jpt_tosql.to_sql("JamPerTrecho", con=meta.bind, if_exists="append", index=False)
