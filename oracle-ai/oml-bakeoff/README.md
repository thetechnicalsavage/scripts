# An OML bake-off across five mining functions

"We used machine learning" is not a result. Being better than the obvious thing is.

So do not assert an algorithm. Train several candidates per capability, record every
one with its metric and its training time, and keep the losers. "Why this model"
becomes a query instead of somebody's recollection.

| Capability | `mining_function` | Candidates | Selection metric |
|---|---|---|---|
| Churn risk | `CLASSIFICATION` | 5 | balanced accuracy |
| Offer propensity | `CLASSIFICATION` | scored live | balanced accuracy |
| Customer segment | `CLUSTERING` | 7 | mean `CLUSTER_PROBABILITY` |
| Bill anomaly | `CLASSIFICATION`, **null target** | 2 | is what it flags extreme |
| Usage forecast | `TIME_SERIES` | several `EXSM` families | MAPE, held-back cycle |
| Add-on affinity | `ASSOCIATION` | Apriori | confidence and lift |

Training is the easy half. **Three of these five have no accuracy at all**, and that
is where the design lives.

## One line worth searching for

Anomaly detection is `CLASSIFICATION` with no target:

```sql
dbms_data_mining.create_model2(
  mining_function     => 'CLASSIFICATION',   -- yes, CLASSIFICATION
  case_id_column_name => 'SUBSCRIBER_ID',
  target_column_name  => null);              -- and this is what makes it one-class
```

## Three errors, all hit for real

| Error | Cause |
|---|---|
| `ORA-40104` | composite `CASE_ID`; the case id must be a single column |
| `ORA-40205` | setting name or value wrong for that algorithm |
| `ORA-40206` | `ODMS_MAX_PARTITIONS` above 32,767 |

## The measurement trap

Scoring an unsupervised detector means asking whether what it flags is genuinely
extreme. The first attempt ranked on a bill-volatility measure that is `NULL` for
prepaid lines, because they have no invoices, and every score collapsed to zero.
Rank on something every row has, or the detector will tell you nothing, quietly.

Tested on synthetic data: 50,000 subscribers, roughly 17.6M daily usage rows.
Synthetic data produces illustrative numbers, not benchmarks.
