"use client";

/**
 * PresetRow — renders a single tenant model-preset row in the presets table
 *
 * Mirrors components/keys/KeyRow.tsx's shape: a TableRow with data cells + a
 * delete action (onDelete callback prop, mirrors KeyRow's onRevoke).
 */

interface Preset {
  preset_name: string;
  alias_key: string;
  target_model: string;
  updated_at: string;
}

import { TableRow, TableCell, Button } from "@/components/ui";

interface PresetRowProps {
  preset: Preset;
  onDelete: (presetName: string, aliasKey: string) => void;
  /** When true, this row's delete is in flight — disable the button to avoid a double-fire */
  isPendingDelete?: boolean;
}

export function PresetRow({ preset, onDelete, isPendingDelete }: PresetRowProps) {
  return (
    <TableRow role="row">
      <TableCell className="font-mono text-xs">{preset.preset_name}</TableCell>
      <TableCell className="font-mono text-xs">{preset.alias_key}</TableCell>
      <TableCell>{preset.target_model}</TableCell>
      <TableCell>{new Date(preset.updated_at).toLocaleDateString()}</TableCell>
      <TableCell>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={isPendingDelete}
          onClick={() => onDelete(preset.preset_name, preset.alias_key)}
        >
          Delete
        </Button>
      </TableCell>
    </TableRow>
  );
}
