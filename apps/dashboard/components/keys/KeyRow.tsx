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

interface KeyRowProps {
  apiKey: ApiKey;
  onRevoke: (keyId: string) => void;
  /** When true, this row is awaiting revocation confirmation — hide the Revoke button */
  isPendingRevoke?: boolean;
}

export function KeyRow({ apiKey, onRevoke, isPendingRevoke }: KeyRowProps) {
  const isRevoked = apiKey.revoked_at !== null;

  return (
    <tr role="row">
      <td>{apiKey.key_id.slice(0, 8)}…</td>
      <td>{apiKey.name}</td>
      <td>{apiKey.prefix}</td>
      <td>{new Date(apiKey.created_at).toLocaleDateString()}</td>
      <td>
        {isRevoked ? (
          <span className="revoked-badge">
            Revoked {apiKey.revoked_at}
          </span>
        ) : (
          <span>active</span>
        )}
      </td>
      <td>
        {!isRevoked && !isPendingRevoke && (
          <button
            type="button"
            onClick={() => onRevoke(apiKey.key_id)}
          >
            Revoke
          </button>
        )}
      </td>
    </tr>
  );
}
