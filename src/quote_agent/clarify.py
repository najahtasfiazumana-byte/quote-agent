"""Step 2 - decide, in code, what must be asked before we can quote.

The rules here decide WHETHER to ask. The LLM is only used afterwards to
phrase the question nicely (see writer.py). This keeps the agent from
guessing a plan, a billing term or a laptop model on the client's behalf.
"""

from __future__ import annotations

from .catalog import FAMILY_LABELS, Catalog
from .models import Clarification, Extraction


def find_clarifications(extraction: Extraction, catalog: Catalog) -> list[Clarification]:
    questions: list[Clarification] = []
    if not extraction.items:
        questions.append(
            Clarification(field="items", question="Which products and quantities would you like us to quote?")
        )
    for idx, item in enumerate(extraction.items):
        product = catalog.get(item.sku)
        if item.family is None and product is None:
            continue  # not in catalog: handled as manual sourcing, not a client question
        label = item.requested_text

        if product is None:
            members = catalog.family_members(item.family)
            questions.append(
                Clarification(
                    item_index=idx,
                    field="sku",
                    question=f"Which {FAMILY_LABELS.get(item.family, 'option')} do you need for \"{label}\"?",
                    options=[m.name for m in members],
                )
            )
        if item.quantity is None:
            questions.append(
                Clarification(item_index=idx, field="quantity", question=f"How many do you need for \"{label}\"?")
            )
        if product is not None:
            if item.billing_term is None and len(product.billing_terms) > 1:
                questions.append(
                    Clarification(
                        item_index=idx,
                        field="billing_term",
                        question=f"For {product.name}, would you prefer an annual commitment or monthly billing? "
                        "Annual is usually cheaper per month.",
                        options=["Annual commitment", "Monthly billing"],
                    )
                )
            elif item.billing_term and item.billing_term not in product.billing_terms:
                questions.append(
                    Clarification(
                        item_index=idx,
                        field="billing_term",
                        question=f"{product.name} is only available as "
                        f"{' or '.join(product.billing_terms)} billing. Is that okay?",
                        options=list(product.billing_terms),
                    )
                )
        elif item.billing_term is None:
            members = catalog.family_members(item.family)
            if members and all(len(m.billing_terms) > 1 for m in members):
                questions.append(
                    Clarification(
                        item_index=idx,
                        field="billing_term",
                        question=f"For \"{label}\", would you prefer an annual commitment or monthly billing?",
                        options=["Annual commitment", "Monthly billing"],
                    )
                )
    return questions
