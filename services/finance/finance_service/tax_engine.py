"""The tax engine, now shared: see ``chirala_common.tax_engine``.

Moved to the common library so booking-core's own postings (cancellation fees,
no-show penalties, room upgrades) are taxed by exactly the same rules as
finance's. Re-exported here so every existing import keeps working.
"""

from chirala_common.tax_engine import (  # noqa: F401 - re-exported
    PAISE,
    SOURCE_CATEGORY,
    TAX_CATEGORIES,
    TaxLine,
    TaxResult,
    _money,
    _quantity,
    compute_tax,
    record_tax_lines,
    resolve_rules,
    tax_breakdown,
)
