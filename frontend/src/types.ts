export interface User { id: string; email: string; created_at: string }
export interface Case { id: string; title: string; description: string | null; created_at: string }

export type EvidenceKind = "reference_image" | "reference_video" | "supporting";
export interface Evidence {
  id: string; case_id: string; kind: EvidenceKind; original_filename: string;
  content_type: string; size_bytes: number; sha256: string; created_at: string; delete_after: string | null;
}

export type ScanStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export interface Scan {
  id: string; case_id: string; evidence_file_id: string; status: ScanStatus;
  created_at: string; started_at: string | null; finished_at: string | null;
  error: string | null; truncated: boolean; truncation_reason: string | null;
  stats: {
    sites_total?: number; sites_done?: number; pages_scanned?: number; images_compared?: number;
    images_failed?: number; matches_found?: number;
    coverage?: Record<DomainStatus, number>;
    reference?: { type: "image" | "video"; keyframes?: number; duration_seconds?: number };
  };
}

export type DomainStatus = "checked" | "no_images" | "blocked" | "unreachable" | "skipped";
export interface DomainResult {
  domain: string; status: DomainStatus; pages_scanned: number; images_compared: number;
  detail: string | null; checked_at: string | null;
}

export type MatchKind = "exact" | "near_duplicate" | "structural" | "probable";
export type ReviewStatus = "potential" | "reviewed" | "verified" | "dismissed";
export interface VideoSignals {
  verification: "sequence_confirmed" | "partial" | "no_frame_match" | "not_checked";
  reason?: string | null; ordered_frames?: number; frames_checked?: number;
  matched_segment?: { start: number; end: number } | null;
  urls?: { url: string; type: string }[];
}
export interface Match {
  id: string; scan_id: string; domain: string; source_page_url: string; image_url: string;
  kind: MatchKind; review_status: ReviewStatus; created_at: string; reviewed_at: string | null;
  review_note: string | null; has_snapshot: boolean;
  signals: { reference?: string; keyframe_times?: number[]; video?: VideoSignals; [k: string]: unknown };
}

export type TakedownStatus =
  | "draft" | "sent" | "acknowledged" | "removed" | "rejected" | "no_response" | "still_live";
export interface Takedown {
  id: string; case_id: string; match_id: string | null; domain: string; contact: string | null;
  contact_verified: boolean; contact_source: string | null; subject: string; body: string;
  status: TakedownStatus; sent_at: string | null; sent_via: string | null; response_note: string | null;
  history: { at: string; from: string | null; to: string; note: string | null }[];
  created_at: string; placeholders_left: string[]; warnings: string[];
}

export interface Channel { id: string; name: string; url: string | null; what: string; who_decides: string; last_checked: string | null }
export interface Channels { note: string; channels: Channel[] }
