"""Генератор данных для `survey_reference.R` (запускается один раз, вывод в git).

    python tests/golden/survey_reference_data.py

Данные синтетические и детерминированные: зерно фиксировано, а CSV лежит
в репозитории, поэтому R и Python читают один и тот же файл.
"""

from pathlib import Path

import numpy as np
import pandas as pd

SIZE = 400


def build() -> pd.DataFrame:
    generator = np.random.default_rng(20261009)
    sex = generator.choice([1, 2], SIZE, p=[0.38, 0.62])
    age = generator.choice([1, 2, 3], SIZE, p=[0.45, 0.35, 0.20])
    region = generator.choice([1, 2, 3, 4], SIZE, p=[0.3, 0.3, 0.25, 0.15])
    group = generator.choice([1, 2, 3], SIZE, p=[0.4, 0.35, 0.25])
    # Связь региона с группой, чтобы хи-квадрат было что находить.
    shift = generator.random(SIZE) < 0.25
    region = np.where(shift & (group == 3), 4, region)
    x1 = generator.normal(5, 2, SIZE).round(3)
    x2 = generator.normal(0, 1, SIZE).round(3)
    noise = generator.normal(0, 1.5 + 0.5 * (group == 3), SIZE)
    y = (2 + 0.6 * x1 - 0.8 * x2 + 0.7 * (group == 2) + 1.2 * (group == 3) + noise).round(3)
    linear = -2.5 + 0.4 * x1 + 0.6 * (sex == 2)
    buy = (generator.random(SIZE) < 1 / (1 + np.exp(-linear))).astype(int)
    weight = np.exp(generator.normal(0, 0.45, SIZE)).round(4)
    return pd.DataFrame(
        {
            "id": np.arange(1, SIZE + 1),
            "sex": sex,
            "age": age,
            "region": region,
            "grp": group,
            "x1": x1,
            "x2": x2,
            "y": y,
            "buy": buy,
            "w": weight,
        }
    )


if __name__ == "__main__":
    build().to_csv(Path(__file__).with_name("survey_reference_data.csv"), index=False)
