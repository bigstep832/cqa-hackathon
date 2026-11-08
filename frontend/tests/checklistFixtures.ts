import type { ChecklistCreateRequest, ChecklistVersion } from '../src/api/client';
import { historicalChecklist } from './evaluationFixtures';

// Synthetic server responses. No scoring, SQLite writes or model calls.
export function savedChecklist(body: ChecklistCreateRequest, version = 2): ChecklistVersion {
  return { ...historicalChecklist, ...body, id: `synthetic-checklist-v${version}`, version,
    criteria: body.criteria.map(criterion => ({ ...criterion, allow_na: criterion.allow_na ?? false, na_condition: criterion.na_condition ?? null,
      weight: criterion.weight === null ? null : String(criterion.weight),
      options: criterion.options.map(option => ({ ...option, flagged: option.flagged ?? false,
        quality: option.quality == null ? null : String(option.quality),
      })),
    })),
  };
}
export function listOf(version: ChecklistVersion) {
  return { items: [{ checklist_id: version.checklist_id, latest_version_id: version.id, name: version.name, version: version.version }] };
}
