export function uploadQuery(course: string, material: string, filename: string, engine: string, indexAfterUpload: boolean) {
  const query = new URLSearchParams({ course: course.trim(), material_type: material, filename, engine });
  // Saving alone is the default. Never infer paid indexing consent from engine selection.
  query.set("index_after_upload", String(engine === "hybrid" && indexAfterUpload));
  return query;
}

export function visibleIndexJobs<T extends { status: string }>(jobs: T[]) {
  const active = jobs.find(job => job.status === "running") ?? jobs.find(job => job.status === "queued") ?? jobs[0] ?? null;
  return { active, waiting: jobs.filter(job => job !== active && job.status === "queued").length };
}

export function versionLabel(source: { document_version?: string; version_status?: string }) {
  if (source.version_status === "stale") return "来源已变化或不可用，请重新提问";
  if (source.document_version) return `资料版本 ${source.document_version.slice(0, 12)}`;
  return "";
}

export function chooseCourse(current: string, courses: { course: string }[]) {
  return current || courses[0]?.course || "";
}
