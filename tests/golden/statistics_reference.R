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

kish_base <- function(w) sum(w)^2 / sum(w^2)

weighted_proportion_reference <- function(a, wa, b, wb, confidence = 0.95) {
  estimates <- c(weighted.mean(a, wa), weighted.mean(b, wb))
  effective <- c(kish_base(wa), kish_base(wb))
  difference <- estimates[1] - estimates[2]
  pooled <- sum(estimates * effective) / sum(effective)
  pooled_se <- sqrt(pooled * (1 - pooled) * sum(1 / effective))
  z <- difference / pooled_se
  p <- 2 * pnorm(-abs(z))
  interval_se <- sqrt(sum(estimates * (1 - estimates) / effective))
  interval <- difference + c(-1, 1) * qnorm(0.975) * interval_se
  list(z = z, p = p, difference = difference, estimates = estimates,
       effective = effective, interval = interval)
}

weighted_variance <- function(x, w) {
  center <- weighted.mean(x, w)
  sum(w * (x - center)^2) / (sum(w) - sum(w^2) / sum(w))
}

weighted_welch_reference <- function(a, wa, b, wb, confidence = 0.95) {
  means <- c(weighted.mean(a, wa), weighted.mean(b, wb))
  variances <- c(weighted_variance(a, wa), weighted_variance(b, wb))
  effective <- c(kish_base(wa), kish_base(wb))
  terms <- variances / effective
  se <- sqrt(sum(terms))
  df <- sum(terms)^2 / sum(terms^2 / (effective - 1))
  difference <- means[1] - means[2]
  statistic <- difference / se
  p <- 2 * pt(-abs(statistic), df)
  interval <- difference + c(-1, 1) * qt(1 - (1 - confidence) / 2, df) * se
  list(t = statistic, df = df, p = p, difference = difference, means = means,
       variances = variances, effective = effective, interval = interval)
}

balance_reference <- function(a, b, confidence = 0.95) {
  estimates <- c(mean(a), mean(b))
  variances <- c(mean(abs(a)) - estimates[1]^2, mean(abs(b)) - estimates[2]^2)
  difference <- estimates[1] - estimates[2]
  se <- sqrt(variances[1] / length(a) + variances[2] / length(b))
  z <- difference / se
  p <- 2 * pnorm(-abs(z))
  interval <- difference + c(-1, 1) * qnorm(1 - (1 - confidence) / 2) * se
  list(z = z, p = p, difference = difference, estimates = estimates,
       variances = variances, interval = interval)
}

references <- list(
  proportion = proportion_reference(70, 100, 50, 100),
  subgroup_rest = proportion_reference(42, 60, 20, 40),
  bonferroni = proportion_reference(63, 100, 49, 100, comparisons = 4),
  small_expected = proportion_reference(1, 30, 8, 30),
  welch = welch_reference(
    10 + (0:41 %% 7),
    8 + (0:37 %% 5)
  ),
  weighted_proportion = weighted_proportion_reference(
    c(rep(1, 20), rep(0, 20)), c(rep(2, 20), rep(1, 20)),
    c(rep(1, 12), rep(0, 28)), rep(1, 40)
  ),
  weighted_welch = weighted_welch_reference(
    10 + (0:41 %% 7), c(rep(2, 21), rep(1, 21)),
    8 + (0:37 %% 5), rep(1, 38)
  ),
  nps_balance = balance_reference(
    c(rep(-1, 10), rep(0, 20), rep(1, 70)),
    c(rep(-1, 30), rep(0, 30), rep(1, 40))
  )
)

options(digits = 17)
dput(references)
