args <- commandArgs(trailingOnly = TRUE)
n <- if (length(args) >= 1) as.integer(args[1]) else 100
set.seed(42); x <- rnorm(n)
dir.create("output", showWarnings = FALSE)
writeLines(toString(c(length(x), mean(x), sd(x))), "output/result.txt")
