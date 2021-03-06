import time
import numpy as np
import pandas as pd
import geopandas as gpd
import numpy as np
from sqlalchemy import extract, select, func
from sqlalchemy.sql import or_, and_
import datetime
from shapely.geometry import Point

from src.data.processing_func import (get_direction, extract_geo_sections)

def extract_jps(meta, date_begin, date_end, periods=None, weekends=False,
                summary=False, skip=None, limit=20000, return_count=False):
    start = time.time()

    jps = meta.tables["JamPerSection"]
    jam = meta.tables["Jam"]
    sctn = meta.tables["Section"]
    mongo_record = meta.tables["MongoRecord"]

    query_count = select([func.count()])

    query_all = select([mongo_record.c.MgrcDateStart,
                    jps.c.JpsId,
                    jps.c.SctnId,
                    jam.c.JamId,
                    jam.c.JamIndLevelOfTraffic,
                    jam.c.JamQtdLengthMeters,
                    jam.c.JamSpdMetersPerSecond,
                    jam.c.JamTimeDelayInSeconds,
                    jam.c.JamDscCoordinatesLonLat])

    queries = [query_count, query_all]

    queries = [q.select_from(mongo_record.join(jam.join(jps), isouter=True)).\
                      where(mongo_record.c.MgrcDateStart.between(date_begin, date_end)) for q in queries]

    if not weekends:
        queries = [q.where(extract("isodow", mongo_record.c.MgrcDateStart).in_(list(range(1,6))))
                   for q in queries]

    if periods:
        or_list=[]
        for t in periods:
            or_list.append(and_(extract("hour", mongo_record.c.MgrcDateStart)>=t[0],
                                extract("hour", mongo_record.c.MgrcDateStart)<t[1]
                                )
                          )
        queries = [q.where(or_(*or_list)) for q in queries]

    query_count, query_all = queries

    if return_count:
        size = query_count.execute().scalar()
        return size

    query_all = query_all.order_by(mongo_record.c.MgrcDateStart, jps.c.JpsId).offset(skip).limit(limit)

    df_jps = pd.read_sql(query_all, meta.bind)
    df_jps["JamSpdKmPerHour"] = df_jps["JamSpdMetersPerSecond"]*3.6
    df_jps[["LonDirection","LatDirection", "MajorDirection"]] = df_jps["JamDscCoordinatesLonLat"].apply(get_direction)
    try:
        df_jps["MgrcDateStart"] = df_jps["MgrcDateStart"].dt.tz_convert("America/Sao_Paulo")
    except AttributeError:
        pass
    df_jps["date"] = pd.to_datetime(df_jps["MgrcDateStart"], utc=True).dt.date
    df_jps["hour"] = df_jps["MgrcDateStart"].astype(str).str[11:13].astype(int)
    df_jps["minute"] = df_jps["MgrcDateStart"].astype(str).str[14:16].astype(int)
    df_jps["period"] = np.sign(df_jps["hour"]-12)

    bins = [0, 14, 29, 44, 59]
    labels = []
    for i in range(1,len(bins)):
        if i==1:
            labels.append(str(bins[i-1]) + " a " + str(bins[i]))
        else:
            labels.append(str(bins[i-1]+1) + " a " + str(bins[i]))

    df_jps['minute_bin'] = pd.cut(df_jps["minute"], bins, labels=labels, include_lowest=True)
    end = time.time()

    processing_time = round(end - start)

    if summary:
        minutos_engarrafados = df_jps["JamId"].nunique()
        n_trechos = df_jps["SctnId"].nunique()

        print("Tempo para carregamento dos dados: " + str(processing_time) + " segundos.")
        print("Number of rows:" + str(len(df_jps)))
        print("Minutos de engarrafamento carregados: " + str(minutos_engarrafados))
        print("Número de trechos abrangidos: " + str(n_trechos))

    return df_jps

def transf_flow_features(df_jps, geo_sections):
    def get_main_direction(x):
        if x["StreetDirection"] == "Norte/Sul":
            return x["LatDirection"]
        elif x["StreetDirection"] == "Leste/Oeste":
            return x["LonDirection"]


    #Get Major Direction from geo_sections
    major_direction = geo_sections["StreetDirection"]
    df_jps = df_jps.join(major_direction, on="SctnId")

    #Create feature dataset
    df_flow_features = df_jps.groupby(["SctnId", "date", "hour",
                     "minute_bin", "LonDirection", "LatDirection", "StreetDirection"]).agg(
                                                          {"JamQtdLengthMeters": ["mean"],
                                                           "JamSpdMetersPerSecond": ["mean"],
                                                           "JamTimeDelayInSeconds": ["mean"],
                                                           "JamIndLevelOfTraffic": ["mean"],
                                                          })
    df_flow_features.columns = ['_'.join(col).strip() for col in df_flow_features.columns.values]
    df_flow_features["JamSpdKmPerHour_mean"] = df_flow_features["JamSpdMetersPerSecond_mean"]*3.6
    columns = {"JamSpdKmPerHour_mean": "Velocidade Média (km/h)",
               "JamQtdLengthMeters_mean": "Fila média (m)",
               "JamTimeDelayInSeconds_mean": "Atraso médio (s)",
               "JamIndLevelOfTraffic_mean": "Nível médio de congestionamento (0 a 5)"
              }
    df_flow_features.reset_index(["LonDirection", "LatDirection", "StreetDirection"], inplace=True)
    df_flow_features["Direction"] = df_flow_features.apply(lambda x: get_main_direction(x), axis=1)
    df_flow_features.set_index("Direction", append=True, inplace=True)
    df_flow_features.rename(columns=columns, inplace=True)
    df_flow_features = df_flow_features[[col for col in columns.values()]]

    return df_flow_features

def transf_flow_labels(geo_sections, path_fluxos):
  
    df_fluxos = pd.read_csv(path_fluxos, sep=';', decimal=',')
    df_fluxos.dropna(subset=["Latitude", "Longitude"], inplace=True)
    df_fluxos["fluxo_Point"] = df_fluxos.apply(lambda x: Point(x["Longitude"], x["Latitude"]), axis=1)
    direction = {"N": "North",
              "S": "South",
              "Norte": "North",
              "Sul": "South",
              "L": "East",
              "O": "West",
              "Leste": "East",
              "Oeste": "West",}
    df_fluxos["Direction"] = df_fluxos["Sentido"].str.split("/", 1).str.get(1).map(direction)
    df_fluxos["date"] = pd.to_datetime(df_fluxos["Data"], dayfirst=True).dt.date

    geo_fluxos = gpd.GeoDataFrame(df_fluxos, crs={'init': 'epsg:4326'}, geometry="fluxo_Point")
    df_flow_labels = gpd.sjoin(geo_fluxos, geo_sections.reset_index(), how="left", op="within")
    df_flow_labels["hour"] = df_flow_labels["Horario"].str[:2].astype(int)
    df_flow_labels["minute_bin"] = df_flow_labels["Horario"].str[3:5] + " a " + df_flow_labels["Horario"].str[12:14]
    df_flow_labels["minute_bin"] = df_flow_labels["minute_bin"].str.replace("00", "0")
    df_flow_labels.set_index("date", inplace=True) #Done separately because set_index to Multiindex convert date type to Timestamp
    df_flow_labels.set_index(["SctnId", "hour", "minute_bin", "Direction"], append=True, inplace=True)
    df_flow_labels.index = df_flow_labels.index.swaplevel(0,1) #So index will be in the same order as df_flow_features
    columns = ['Endereco', 'SctnDscNome', 'SctnQtdMetrosAcumulados', 'Corredor', 'Ciclofaixa', 'Numero de faixas', 'Sentido', 'Equipamento', '00 a 10',
               '11 a 20', '21 a 30', '31 a 40', '41 a 50', '51 a 60', '61 a 70',
               '71 a 80', '81 a 90', '91 a 100', 'Acima de 100', 'Total',
              ]
    df_flow_labels = df_flow_labels[columns]
  
    return df_flow_labels