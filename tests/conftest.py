import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def crosswalk() -> pd.DataFrame:
    """Small ZIP-to-ZCTA crosswalk: 95945 splits, 95712 is a PO-box ZIP folded into 95945."""
    return pd.DataFrame(
        {
            "zip": ["95945", "95945", "95712", "95959"],
            "zcta": ["95945", "95949", "95945", "95959"],
            "weight": [0.9, 0.1, 1.0, 1.0],
        }
    )
