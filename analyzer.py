import json
import re
import time
from typing import Any, Dict

import pandas as pd

from config import get_llm
from data_loader import COLUMN_KEYWORDS, detect_dataset_type, find_column, get_statistics
from models import AnalysisResponse, BusinessMetrics, DatasetSummary
from prompts import qa_prompt, recommendation_prompt, summary_prompt

# A structured-output call can fail for two different reasons: the request was
# rate-limited, or the model answered without a well-formed tool call. The Groq
# client retries the first kind; nothing retries the second, and it is the one
# that makes an endpoint fail intermittently for no visible reason.
STRUCTURED_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.5


def invoke_structured(chain, payload: Dict[str, Any]):
    """Invoke a structured-output chain, retrying a malformed response."""
    last_error: Exception | None = None
    for attempt in range(STRUCTURED_ATTEMPTS):
        try:
            return chain.invoke(payload)
        except Exception as exc:  # noqa: BLE001 - retry any transient failure
            last_error = exc
            if attempt < STRUCTURED_ATTEMPTS - 1:
                time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    raise last_error


def summarize_without_llm(df: pd.DataFrame) -> DatasetSummary:
    """Build a dataset summary from Pandas alone.

    Every figure on the dashboard except this summary is calculated locally, so a
    model that is rate-limited or answering without a well-formed tool call should
    cost the user their AI commentary - not their dataset.
    """
    stats = get_statistics(df)
    columns = ", ".join(str(c) for c in df.columns)

    notes = []
    missing = {k: v for k, v in stats["missing_values"].items() if v}
    if missing:
        notes.append(
            "Missing values: " + ", ".join(f"{k} ({v})" for k, v in missing.items()) + "."
        )
    else:
        notes.append("No missing values detected in any column.")
    if stats["duplicate_rows"]:
        notes.append(f"{stats['duplicate_rows']} duplicate rows found.")
    else:
        notes.append("No duplicate rows found.")
    notes.append("AI commentary is unavailable right now, so these notes are from the data itself.")

    return DatasetSummary(
        dataset_type=detect_dataset_type(df),
        summary=(
            f"{stats['row_count']} rows across {stats['column_count']} columns: {columns}. "
            "Metrics, preview and statistics below are calculated directly from your data."
        ),
        data_quality_notes=notes,
        suggested_questions=[
            "What is the total revenue?",
            "Which region performs best?",
            "What are the top products?",
            "Give me recommendations based on this data.",
        ],
    )


def generate_initial_insights(df: pd.DataFrame, metrics: BusinessMetrics, llm=None) -> DatasetSummary:
    """Summarize the dataset, flag data quality issues, and suggest questions to ask."""
    llm = llm or get_llm()
    structured_llm = llm.with_structured_output(DatasetSummary, method="function_calling")
    chain = summary_prompt | structured_llm

    stats = get_statistics(df)
    result: DatasetSummary = invoke_structured(
        chain,
        {
            "dataset_type": detect_dataset_type(df),
            "columns": ", ".join(str(c) for c in df.columns),
            "row_count": stats["row_count"],
            "missing_values": stats["missing_values"] or "None",
            "duplicate_rows": stats["duplicate_rows"],
            "sample_rows": df.head(5).to_json(orient="records"),
            "metrics": metrics.model_dump_json(indent=2),
        },
    )
    return result


def detect_intent(question: str, df: pd.DataFrame) -> Dict[str, Any]:
    """Map a natural-language question to one of the app's supported analysis types."""
    q = question.lower()

    if "recommend" in q or "suggestion" in q:
        return {"type": "recommendation"}

    if "customer" in q and any(kw in q for kw in ["how many", "number of", "count", "total"]):
        return {"type": "customer_count"}

    if "region" in q and any(kw in q for kw in ["highest", "top", "best", "most"]):
        return {"type": "top_region"}

    if "product" in q and any(kw in q for kw in ["top", "best", "highest"]):
        match = re.search(r"top\s+(\d+)", q)
        top_n = int(match.group(1)) if match else 5
        return {"type": "top_products", "top_n": top_n}

    if "profit" in q:
        return {"type": "total_profit"}

    if "revenue" in q:
        return {"type": "total_revenue"}

    if "sales" in q:
        return {"type": "total_sales"}

    if "expense" in q or "cost" in q:
        return {"type": "total_expenses"}

    return {"type": "general"}


def execute_analysis(df: pd.DataFrame, intent: Dict[str, Any], metrics: BusinessMetrics) -> Dict[str, Any]:
    """Run the calculation for the given intent using Pandas and the pre-computed metrics."""
    intent_type = intent["type"]

    simple_metric_map = {
        "total_sales": ("Total Sales", metrics.total_sales),
        "total_revenue": ("Total Revenue", metrics.total_revenue),
        "total_profit": ("Total Profit", metrics.total_profit),
        "total_expenses": ("Total Expenses", metrics.total_expenses),
        "customer_count": ("Total Customers", metrics.total_customers),
    }

    if intent_type in simple_metric_map:
        label, value = simple_metric_map[intent_type]
        if value is None:
            return {"error": f"No matching column found to calculate {label}."}
        return {"metric": label, "value": value}

    if intent_type == "top_region":
        region_col = find_column(df, COLUMN_KEYWORDS["region"])
        value_col = find_column(df, COLUMN_KEYWORDS["sales"]) or find_column(df, COLUMN_KEYWORDS["revenue"])
        if not region_col or not value_col:
            return {"error": "No region or sales/revenue column found in the dataset."}
        grouped = df.groupby(region_col)[value_col].sum().sort_values(ascending=False)
        return {
            "top_region": grouped.index[0],
            "value": float(grouped.iloc[0]),
            "breakdown": grouped.to_dict(),
        }

    if intent_type == "top_products":
        product_col = find_column(df, COLUMN_KEYWORDS["product"])
        value_col = find_column(df, COLUMN_KEYWORDS["sales"]) or find_column(df, COLUMN_KEYWORDS["revenue"])
        if not product_col or not value_col:
            return {"error": "No product or sales/revenue column found in the dataset."}
        top_n = intent.get("top_n", 5)
        grouped = df.groupby(product_col)[value_col].sum().sort_values(ascending=False).head(top_n)
        return {"top_products": grouped.to_dict()}

    # "general" fallback: try to match the question against any column name directly
    return {"note": "This question didn't match a supported analysis type."}


def explain_result(
    question: str,
    intent: Dict[str, Any],
    result: Dict[str, Any],
    dataset_summary: DatasetSummary,
    metrics: BusinessMetrics,
    llm=None,
) -> AnalysisResponse:
    """Turn a Pandas result (or general request) into a business-friendly AnalysisResponse."""
    llm = llm or get_llm()
    structured_llm = llm.with_structured_output(AnalysisResponse, method="function_calling")

    if intent["type"] == "recommendation":
        chain = recommendation_prompt | structured_llm
        return invoke_structured(
            chain,
            {
                "question": question,
                "dataset_summary": dataset_summary.summary,
                "metrics": metrics.model_dump_json(indent=2),
            },
        )

    chain = qa_prompt | structured_llm
    return invoke_structured(
        chain,
        {
            "question": question,
            "analysis_type": intent["type"],
            "result_json": json.dumps(result, indent=2, default=str),
        }
    )
