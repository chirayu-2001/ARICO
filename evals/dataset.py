"""LangSmith evaluation dataset for ARICO.

Five examples — one per store scenario. Each has a known ground-truth root cause
baked into the seeded database, so we can judge whether the agent found it.

Revenue values are set deliberately low so all examples auto-execute without
triggering HITL, letting the eval harness collect full outputs cleanly.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DATASET_NAME = "arico-store-scenarios-v2"

# Each example:
#   inputs         → passed to the ARICO graph
#   reference_outputs → used by evaluators to judge correctness
EXAMPLES = [
    {
        "inputs": {
            "alert": {
                "store_id": "101",
                "loss_reason": "Footwear sales have dropped noticeably over the past two weeks",
                "revenue_at_risk": 5000.0,
                "product_category": "shoes",
                "estimated_units_at_risk": 20,
            }
        },
        "reference_outputs": {
            "expected_action_needed": True,
            "expected_root_cause_keywords": ["metro shoes", "promo", "competitor", "discount", "20%"],
            "expected_campaign_hint": "Should recommend a counter-promotion or matching discount to offset Metro Shoes' 20% off promo at Connaught Place",
            "store_name": "Connaught Place Store",
        },
    },
    {
        "inputs": {
            "alert": {
                "store_id": "202",
                "loss_reason": "Classic Runner revenue has collapsed in the last two weeks",
                "revenue_at_risk": 5000.0,
                "product_category": "shoes",
                "estimated_units_at_risk": 20,
            }
        },
        "reference_outputs": {
            "expected_action_needed": True,
            "expected_root_cause_keywords": ["stockout", "out of stock", "inventory", "reorder", "SHOE-001", "Classic Runner"],
            "expected_campaign_hint": "Should recommend urgent restocking of SHOE-001 and/or promotion of alternate SKUs (SHOE-002/SHOE-003) while stock is replenished",
            "store_name": "Phoenix Palladium Outlet",
        },
    },
    {
        "inputs": {
            "alert": {
                "store_id": "303",
                "loss_reason": "Sales seem lower compared to last month",
                "revenue_at_risk": 5000.0,
                "product_category": "shoes",
                "estimated_units_at_risk": 10,
            }
        },
        "reference_outputs": {
            "expected_action_needed": False,
            "expected_root_cause_keywords": ["monsoon", "seasonal", "benchmark", "expected", "normal"],
            "expected_campaign_hint": "No campaign should be generated — this is a normal seasonal dip matching the Bengaluru June monsoon benchmark",
            "store_name": "Indiranagar Store",
        },
    },
    {
        "inputs": {
            "alert": {
                "store_id": "404",
                "loss_reason": "Customer complaints increasing and repeat purchases declining",
                "revenue_at_risk": 5000.0,
                "product_category": "shoes",
                "estimated_units_at_risk": 20,
            }
        },
        "reference_outputs": {
            "expected_action_needed": True,
            "expected_root_cause_keywords": ["quality", "defect", "reviews", "sole", "complaints", "SHOE-001", "Classic Runner"],
            "expected_campaign_hint": "Should recommend a quality investigation and/or loyalty/recovery campaign for affected SHOE-001 customers",
            "store_name": "Anna Nagar Store",
        },
    },
    {
        "inputs": {
            "alert": {
                "store_id": "505",
                "loss_reason": "Footfall and shoe sales declining steadily over the past 6 weeks",
                "revenue_at_risk": 5000.0,
                "product_category": "shoes",
                "estimated_units_at_risk": 20,
            }
        },
        "reference_outputs": {
            "expected_action_needed": True,
            "expected_root_cause_keywords": ["decathlon", "competitor", "store opening", "nearby", "Kolkata"],
            "expected_campaign_hint": "Should recommend a brand differentiation or loyalty campaign to retain customers against Decathlon's permanent presence",
            "store_name": "South City Mall Store",
        },
    },
]


def create_or_update_dataset(client) -> str:
    """Create the dataset in LangSmith if it doesn't exist, then return its name.

    If the dataset already exists, existing examples are left untouched.
    New examples are appended. This makes it safe to re-run.
    """
    existing = {ds.name for ds in client.list_datasets()}

    if DATASET_NAME not in existing:
        dataset = client.create_dataset(
            DATASET_NAME,
            description="5 Indian retail store scenarios for ARICO evaluation. "
                        "Each has a distinct root cause baked into the SQLite DB.",
        )
        logger.info(f"Created LangSmith dataset: {DATASET_NAME}")
    else:
        dataset = client.read_dataset(dataset_name=DATASET_NAME)
        logger.info(f"Using existing LangSmith dataset: {DATASET_NAME}")

    existing_inputs = {
        ex.inputs.get("alert", {}).get("store_id")
        for ex in client.list_examples(dataset_id=dataset.id)
    }

    new_examples = [
        ex for ex in EXAMPLES
        if ex["inputs"]["alert"]["store_id"] not in existing_inputs
    ]

    if new_examples:
        client.create_examples(
            inputs=[ex["inputs"] for ex in new_examples],
            outputs=[ex["reference_outputs"] for ex in new_examples],
            dataset_id=dataset.id,
        )
        logger.info(f"Added {len(new_examples)} example(s) to dataset")
    else:
        logger.info("All examples already in dataset — nothing to add")

    return DATASET_NAME
