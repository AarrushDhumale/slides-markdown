// Quotas while there is no real sign-in. Together with the worker's spend caps
// (S2M_MAX_USD_PER_JOB / S2M_MAX_USD_PER_MONTH) they bound the worst case.
export const LIMITS = {
  maxPdfBytes: 50 * 1024 * 1024,
  maxPdfsPerNotebook: 12,
  uploadsPerDay: 40,
  jobsPerDay: 12,
  globalQueue: 25,
  explainsPerHour: 40,
  explainWaitMs: 15_000, // local worker must start answering within this, else the cloud answers
  workerFreshMs: 60_000,
};

export const LEVELS = ["summary", "learner", "proficient", "dockey"] as const;
export type Level = (typeof LEVELS)[number];
