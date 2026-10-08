# Independent reference calculations for tests/golden/survey_reference.json.
#
# Needs R >= 4.6 with packages `survey` and `jsonlite`:
#   Rscript tests/golden/survey_reference.R
# Reads survey_reference_data.csv next to this script and overwrites
# survey_reference.json. Every number in the JSON comes from R itself.

suppressMessages({
  library(survey)
  library(jsonlite)
})

args <- commandArgs(trailingOnly = FALSE)
script <- sub("^--file=", "", args[grep("^--file=", args)])
here <- if (length(script)) dirname(normalizePath(script)) else "tests/golden"
data <- read.csv(file.path(here, "survey_reference_data.csv"))
n <- nrow(data)
data$w1 <- data$w / mean(data$w)  # start weight, mean 1
weighted <- svydesign(ids = ~1, weights = ~w, data = data)
plain <- svydesign(ids = ~1, weights = ~rep(1, n), data = data)
started <- svydesign(ids = ~1, weights = ~w1, data = data)

rao_scott <- function(formula) {
  test <- svychisq(formula, weighted, statistic = "F")
  list(
    rows = all.vars(formula)[1], columns = all.vars(formula)[2],
    statistic = unname(test$statistic),
    df = unname(test$parameter),
    p_value = unname(test$p.value)
  )
}

coefficients_of <- function(table, model = NULL) {
  result <- list(
    estimate = unname(table[, 1]), std_error = unname(table[, 2]),
    statistic = unname(table[, 3]), p_value = unname(table[, 4])
  )
  if (!is.null(model)) {
    interval <- confint(model)
    result$ci_low <- unname(interval[, 1])
    result$ci_high <- unname(interval[, 2])
  }
  result
}

normalized <- function(design) {
  values <- weights(design)
  unname(values / mean(values))
}

sex_targets <- c(0.48, 0.52)
age_targets <- c(0.30, 0.40, 0.30)

rake_case <- function(design) {
  total <- sum(weights(design))
  raked <- rake(
    design, list(~sex, ~age),
    list(
      data.frame(sex = 1:2, Freq = total * sex_targets),
      data.frame(age = 1:3, Freq = total * age_targets)
    ),
    control = list(maxit = 1000, epsilon = 1e-13)
  )
  normalized(raked)
}

cell_targets <- data.frame(
  sex = c(1, 1, 1, 2, 2, 2), age = c(1, 2, 3, 1, 2, 3),
  share = c(0.14, 0.19, 0.15, 0.16, 0.21, 0.15)
)

cells_case <- function(design) {
  data$cell <- interaction(data$sex, data$age)
  design <- update(design, cell = interaction(sex, age))
  total <- sum(weights(design))
  population <- data.frame(
    cell = interaction(cell_targets$sex, cell_targets$age),
    Freq = total * cell_targets$share
  )
  normalized(postStratify(design, ~cell, population))
}

greg_case <- function(design, bounds = c(-Inf, Inf)) {
  total <- sum(weights(design))
  population <- c(
    "(Intercept)" = total,
    "factor(sex)2" = total * sex_targets[2],
    "factor(age)2" = total * age_targets[2],
    "factor(age)3" = total * age_targets[3]
  )
  calibrated <- calibrate(
    design, ~factor(sex) + factor(age), population,
    calfun = "linear", bounds = bounds, maxit = 200, epsilon = 1e-13
  )
  list(
    bounds = if (all(is.finite(bounds))) bounds else NULL,
    weights = normalized(calibrated),
    g = unname(weights(calibrated) / weights(design))
  )
}

linear_plain <- lm(y ~ x1 + x2 + factor(grp), data = data)
linear_summary <- summary(linear_plain)
linear_weighted <- svyglm(y ~ x1 + x2 + factor(grp), design = weighted)
# glm по умолчанию останавливается при epsilon = 1e-8 по девиансу, и
# коэффициенты точны лишь до ~1e-8; эталону нужна полная сходимость.
tight <- glm.control(epsilon = 1e-14, maxit = 100)
logistic_plain <- glm(buy ~ x1 + factor(sex), family = binomial(), data = data, control = tight)
logistic_weighted <- svyglm(
  buy ~ x1 + factor(sex), design = weighted, family = quasibinomial(), control = tight
)
pearson <- cor.test(data$x1, data$y, method = "pearson")
spearman <- suppressWarnings(cor.test(data$x1, data$y, method = "spearman", exact = FALSE))
chi_plain <- chisq.test(table(data$region, data$grp), correct = FALSE)
welch <- oneway.test(y ~ factor(grp), data = data, var.equal = FALSE)
fisher <- fisher.test(table(data$sex, data$buy))

references <- list(
  provenance = list(
    engine = paste("R", paste(R.version$major, R.version$minor, sep = ".")),
    survey = as.character(packageVersion("survey")),
    script = "survey_reference.R",
    data = "survey_reference_data.csv",
    reference_locked_on = format(Sys.Date())
  ),
  rao_scott = list(
    rao_scott(~region + grp),
    rao_scott(~sex + age),
    rao_scott(~sex + buy),
    rao_scott(~age + region)
  ),
  chi_square = list(
    statistic = unname(chi_plain$statistic), df = unname(chi_plain$parameter),
    p_value = chi_plain$p.value
  ),
  welch_anova = list(
    statistic = unname(welch$statistic), df = unname(welch$parameter),
    p_value = welch$p.value
  ),
  fisher = list(p_value = fisher$p.value),
  pearson = list(r = unname(pearson$estimate), p_value = pearson$p.value),
  spearman = list(r = unname(spearman$estimate), p_value = spearman$p.value),
  linear = c(
    coefficients_of(coef(linear_summary)),
    list(
      r_squared = linear_summary$r.squared,
      adjusted_r_squared = linear_summary$adj.r.squared,
      f_statistic = unname(linear_summary$fstatistic[1]),
      f_p_value = unname(pf(
        linear_summary$fstatistic[1], linear_summary$fstatistic[2],
        linear_summary$fstatistic[3], lower.tail = FALSE
      ))
    )
  ),
  linear_weighted = c(
    coefficients_of(coef(summary(linear_weighted)), linear_weighted),
    list(df_residual = linear_weighted$df.residual)
  ),
  logistic = coefficients_of(coef(summary(logistic_plain))),
  logistic_weighted = c(
    coefficients_of(coef(summary(logistic_weighted)), logistic_weighted),
    list(df_residual = logistic_weighted$df.residual)
  ),
  rake = list(
    sex = sex_targets, age = age_targets,
    plain = rake_case(plain), started = rake_case(started)
  ),
  cells = list(
    targets = cell_targets, plain = cells_case(plain), started = cells_case(started)
  ),
  greg = list(
    sex = sex_targets, age = age_targets,
    plain = greg_case(plain), started = greg_case(started),
    bounded = greg_case(plain, c(0.6, 1.8))
  )
)

write_json(
  references, file.path(here, "survey_reference.json"),
  digits = NA, auto_unbox = TRUE, pretty = TRUE
)
cat("written", file.path(here, "survey_reference.json"), "\n")
