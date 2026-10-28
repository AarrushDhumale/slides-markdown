// Quotas while there is no real sign-in. Together with the worker's spend caps
// (S2M_MAX_USD_PER_JOB / S2M_MAX_USD_PER_MONTH) they bound the worst case.
export const LIMITS = {
  maxPdfBytes: 50 * 1024 * 1024,
  maxPdfsPerNotebook: 12,
  uploadsPerDay: 40,
  jobsPerDay: 12,
  globalQueue: 25,
  explainsPerHour: 40,
  rewritesPerHour: 20, // per-section level rewrites that need a model call (cached ones are free)
  explainWaitMs: 15_000, // local worker must start answering within this, else the cloud answers
  workerFreshMs: 60_000,
};

export const LEVELS = ["summary", "notes", "learner", "proficient", "donkey"] as const;
export type Level = (typeof LEVELS)[number];

/** Levels a single section can be rewritten at (the condensed levels are made from the learner doc). */
export const REWRITE_LEVELS = ["learner", "proficient", "donkey"] as const;
export type RewriteLevel = (typeof REWRITE_LEVELS)[number];
