# app\models\qdrant\metric_info_qdrant.py
from typing import TypedDict


class MetricInfoQdrant(TypedDict):
    id:str
    name:str
    description:str
    relevant_columns:list
    alias:list