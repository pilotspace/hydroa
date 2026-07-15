"use client";

/**
 * KeyRow — renders a single API key row in the keys table
 */

interface ApiKey {
  key_id: string;
  name: string;
  prefix: string;
  created_at: string;
  revoked_at: string | null;
}

import { TableRow, TableCell, Badge, Button } from "@/components/ui";
import { formatTimestamp } from "@/lib/format";

interface KeyRowProps {
  apiKey: ApiKey;
  onRevoke: (keyId: string) => void;
  /** When true, this row is awaiting revocation confirmation — hide the Revoke button */
  isPendingRevoke?: boolean;
}

export function KeyRow({ apiKey, onRevoke, isPendingRevoke }: KeyRowProps) {
  const isRevoked = apiKey.revoked_at !== null;

  return (
    <TableRow role="row">
      <TableCell className="font-mono text-xs">{apiKey.key_id.slice(0, 8)}…</TableCell>
      <TableCell>{apiKey.name}</TableCell>
      <TableCell className="font-mono text-xs">{apiKey.prefix}</TableCell>
      <TableCell>{new Date(apiKey.created_at).toLocaleDateString()}</TableCell>
      <TableCell>
        {isRevoked ? (
          <Badge variant="destructive" className="revoked-badge">
            Revoked {formatTimestamp(apiKey.revoked_at)}
          </Badge>
        ) : (
          <Badge variant="secondary">active</Badge>
        )}
      </TableCell>
      <TableCell>
        {!isRevoked && !isPendingRevoke && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onRevoke(apiKey.key_id)}
          >
            Revoke
          </Button>
        )}
      </TableCell>
    </TableRow>
  );
}
