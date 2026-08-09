# Golden-набор статистики

`statistics_reference.json` содержит численные эталоны, рассчитанные независимо от
Python-кода приложения. Воспроизводящий скрипт `statistics_reference.R` использует
только Base R и печатает результаты pooled two-sided z-test, unpooled Wald CI и
Welch t-test с исходными выборками из JSON.

Используемые независимые примитивы описаны в официальной документации R:
[`pnorm`/`qnorm`](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/Normal.html)
и [`t.test` с Welch–Satterthwaite df](https://stat.ethz.ch/R-manual/R-devel/library/stats/html/t.test.html).

Проверка выполняется так:

```text
Rscript tests/golden/statistics_reference.R
pytest tests/test_golden_statistics.py
```

`pipeline_statistics.sha256` имеет другую роль: это регрессионный отпечаток полного
`statistics.txt` после замены динамического времени на `<TIMESTAMP>`. Он гарантирует,
что ни одна строка аудита не изменилась незаметно, но сам по себе не является
независимым статистическим источником. Обновлять этот SHA допустимо только после
проверки чисел по независимому reference и осознанного просмотра изменения формата.

Текущий пакет покрывает z/Welch/Subgroup-Rest, приближённые weighted z/Welch,
NPS balance, MR `counted_value=2` и SPSS user-missing `99`. Волны и расширенные
NPS/CSAT pipeline-сценарии добавляются отдельными golden cases.
