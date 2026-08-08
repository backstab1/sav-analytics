# Independent reference calculations for tests/golden/statistics_reference.json.
# Run with base R only: Rscript tests/golden/statistics_reference.R

proportion_reference <- function(x1, n1, x2, n2, confidence = 0.95, comparisons = 1) {
  estimates <- c(x1 / n1, x2 / n2)
  difference <- estimates[1] - estimates[2]
  pooled <- (x1 + x2) / (n1 + n2)
  expected <- c(n1 * pooled, n1 * (1 - pooled), n2 * pooled, n2 * (1 - pooled))
  pooled_se <- sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
  z <- difference / pooled_se
  p <- 2 * pnorm(-abs(z))
  alpha <- (1 - confidence) / comparisons
  unpooled_se <- sqrt(estimates[1] * (1 - estimates[1]) / n1 +
                      estimates[2] * (1 - estimates[2]) / n2)
  interval <- difference + c(-1, 1) * qnorm(1 - alpha / 2) * unpooled_se
  list(z = z, p = p, difference = difference, alpha = alpha,
       interval = interval, expected = expected)
}

welch_reference <- function(a, b, confidence = 0.95) {
  difference <- mean(a) - mean(b)
  variance_terms <- c(var(a) / length(a), var(b) / length(b))
  se <- sqrt(sum(variance_terms))
  df <- sum(variance_terms)^2 /
    (variance_terms[1]^2 / (length(a) - 1) + variance_terms[2]^2 / (length(b) - 1))
  statistic <- difference / se
  p <- 2 * pt(-abs(statistic), df)
  interval <- difference + c(-1, 1) * qt(1 - (1 - confidence) / 2, df) * se
  list(t = statistic, df = df, p = p, difference = difference, interval = interval)
}

references <- list(
  proportion = proportion_reference(70, 100, 50, 100),
  subgroup_rest = proportion_reference(42, 60, 20, 40),
  bonferroni = proportion_reference(63, 100, 49, 100, comparisons = 4),
  small_expected = proportion_reference(1, 30, 8, 30),
  welch = welch_reference(
    10 + (0:41 %% 7),
    8 + (0:37 %% 5)
  )
)

options(digits = 17)
dput(references)
