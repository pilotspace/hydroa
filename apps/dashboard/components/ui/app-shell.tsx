"use client";

import * as React from "react";
import { Activity, BarChart3, Bell, Boxes, Brain, Clapperboard, ClipboardList, Eye, FolderArchive, GaugeCircle, HeartPulse, Hexagon, KeyRound, Layers, LogOut, Menu, MessageSquare, Mic, Receipt, ScrollText, Settings, ShieldCheck, Shuffle, Tags, Users } from "lucide-react";
import { cn } from "@/lib/cn";
import { bffAuthPost } from "@/lib/bff-client";
import {
  Sidebar,
  SidebarBrand,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarItem,
  SidebarTrigger,
} from "./sidebar";
import { ThemeToggle } from "./theme-toggle";
import { Reveal } from "./motion";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "./dialog";

/**
 * The responsive, accessible application shell every dashboard surface inherits — rebuilt
 * (v23) on the token-driven Sidebar parts: a branded, collapsible desktop rail; a mobile
 * sheet (hamburger → Dialog); a theme toggle; and a user-identity footer.
 *
 * The frozen v13 shell contract is preserved EXACTLY: a skip-link to #main as the FIRST
 * focusable element, a single Primary <nav> landmark (the desktop rail — the mobile sheet's
 * nav is labelled distinctly and only mounted when open), a <main id="main"> landmark, and a
 * responsive `lg:flex-row` root (stacked on mobile → row from the lg breakpoint).
 */

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  /**
   * Minimum role that can use the page the link points to. `"admin"` means the
   * page's GET 403s on a `member` (owner|admin only) — so the link is hidden from
   * members. `"owner"` means the page's GET 403s on anyone but the tenant OWNER
   * (e.g. `/admin/presets` — a strictly OWNER-only admin API, unlike the
   * owner-or-admin pages above) — hidden from both member and admin. Omitted ⇒
   * any authenticated role may see the link. The gateway remains the source of
   * truth — this is UX-only and FAILS OPEN (an unrecognized/loading role sees
   * every link rather than risk hiding one a user is actually entitled to).
   */
  minRole?: "admin" | "owner";
}

/** A labeled section of the primary nav (v7 premium IA — 4 workflow groups, Tin 2026-07-06). */
export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * The primary nav grouped into 4 workflow sections. Each item keeps its `minRole`
 * denylist; a group whose items are ALL role-hidden is not rendered (a member never
 * sees an empty "Govern" header — see `visibleGroups`). `Settings` is pinned separately
 * below the groups. Every `href` stays under `/app/...` (marketing-shell source guard).
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Playground",
    items: [
      { href: "/app/chat", label: "Chat", icon: MessageSquare },
      { href: "/app/voice", label: "Voice", icon: Mic },
      { href: "/app/memory", label: "Memory", icon: Brain },
      { href: "/app/artifacts", label: "Artifacts", icon: FolderArchive },
      { href: "/app/vision", label: "Vision", icon: Eye },
      { href: "/app/video", label: "Video", icon: Clapperboard },
    ],
  },
  {
    label: "Insights",
    items: [
      { href: "/app/usage", label: "Usage", icon: BarChart3 },
      { href: "/app/spend", label: "Spend", icon: Receipt },
    ],
  },
  {
    label: "Configure",
    items: [
      { href: "/app/keys", label: "API Keys", icon: KeyRound },
      { href: "/app/presets", label: "Model Presets", icon: Shuffle, minRole: "owner" },
      { href: "/app/models", label: "Models", icon: Boxes, minRole: "admin" },
      { href: "/app/routing", label: "Routing", icon: Activity, minRole: "admin" },
      { href: "/app/batches", label: "Batches", icon: Layers, minRole: "admin" },
    ],
  },
  {
    label: "Govern",
    items: [
      { href: "/app/teams", label: "Teams", icon: Users, minRole: "admin" },
      { href: "/app/members", label: "Members", icon: Users, minRole: "admin" },
      { href: "/app/alerts", label: "Alerts", icon: Bell, minRole: "admin" },
      { href: "/app/audit", label: "Audit", icon: ClipboardList, minRole: "admin" },
      { href: "/app/logs", label: "Logs", icon: ScrollText, minRole: "admin" },
      { href: "/app/health", label: "Health", icon: HeartPulse, minRole: "admin" },
      { href: "/app/slo", label: "SLO", icon: GaugeCircle, minRole: "admin" },
      {
        href: "/app/guardrail-analytics",
        label: "Guardrail Analytics",
        icon: ShieldCheck,
        minRole: "admin",
      },
    ],
  },
];

/** Pinned utility item — rendered below the workflow groups, after a hairline separator. */
const SETTINGS_ITEM: NavItem = { href: "/app/settings", label: "Settings", icon: Settings };

/**
 * Flattened list of every primary nav item — PRESERVED as the stable export several
 * call sites/tests depend on (`NAV_ITEMS.find(i => i.href === …)`, the marketing-shell
 * href source-scan). Derived from the groups so the two never drift.
 */
export const NAV_ITEMS: NavItem[] = [...NAV_GROUPS.flatMap((g) => g.items), SETTINGS_ITEM];

const BRAND = "Hydroa";

export interface AppShellProps {
  children: React.ReactNode;
  /** The active route path, used to mark the current nav item. */
  activePath?: string;
  /**
   * The current user's role (owner|admin|member). When `"member"`, admin-only
   * AND owner-only links are hidden; when `"admin"`, owner-only links are also
   * hidden (admin is not owner). Any other value — including null/undefined
   * while the identity is still loading or failed — FAILS OPEN (all links
   * shown); the gateway still enforces RBAC on navigate, so no one is locked
   * out of their nav.
   */
  role?: string | null;
  /** The signed-in user's email, shown in the sidebar footer when present. */
  userEmail?: string | null;
  /**
   * impersonation-ui TASK.md §3 CONTRACT M6 — additive, optional. Rendered between
   * the skip-link and the fixed-viewport `lg:flex-row` row (after the skip-link in
   * DOM order, so the skip-link stays the first focusable element — the frozen
   * contract). Absent (every existing call site) → byte-identical default
   * rendering, including the row's own `lg:h-screen` height class.
   */
  banner?: React.ReactNode;
  /**
   * command-palette TASK.md §3 CONTRACT M2 — additive, optional. Rendered once,
   * immediately after `{banner}` (before the `Dialog` wrapper) — a
   * no-visual-footprint insertion, since `PlatformCommandPalette`'s own output is
   * a Radix-portaled Dialog plus a CSS-`fixed` trigger button, so its literal
   * position in this JSX tree carries no layout effect, unlike `banner` (which
   * reserves real in-flow height, see the height-class comment below). Absent
   * (every session whose role isn't exactly "superadmin") → byte-identical
   * output to before this task; no other line in this component changes.
   */
  commandPalette?: React.ReactNode;
}

/**
 * The single RBAC-visibility predicate for a nav item — the DENYLIST/fail-open shape:
 * an `admin` page is hidden from a `member`; an `owner` page is hidden from `member` and
 * `admin`; everything else (including a null/undefined loading/failed role) is SHOWN, since
 * the gateway is the real enforcement (this is UX-only). Shared by every group.
 */
function itemVisible(item: NavItem, role?: string | null): boolean {
  if (item.minRole === "admin" && role === "member") return false;
  if (item.minRole === "owner" && (role === "member" || role === "admin")) return false;
  return true;
}

/** Filter each group's items by role, then drop any group left empty (no orphan header). */
function visibleGroups(role?: string | null): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    label: group.label,
    items: group.items.filter((item) => itemVisible(item, role)),
  })).filter((group) => group.items.length > 0);
}

/**
 * "Platform" -> "Tenants" (admin-console-ui, TASK.md M3) is deliberately an
 * ALLOWLIST — visible if and only if role === "superadmin" EXACTLY — not a
 * byte-literal copy of `itemVisible`'s existing DENYLIST/fail-open shape
 * above. Every item in NAV_ITEMS fails OPEN (shown while role is still
 * loading/null, since the gateway is the real enforcement); this ONE entry
 * fails CLOSED instead, because it is the single link whose mere presence
 * discloses a cross-tenant admin surface to ~100% of ordinary paying
 * customers on every cold load if it followed the same fail-open default —
 * a real information-disclosure/trust cost, not just a transient UX nicety
 * (§1 Framings weighed, ⚠ Assumption #3). Zero changes to `visibleItems` or
 * any of the 19 existing NAV_ITEMS entries.
 */
function showPlatformNav(role?: string | null): boolean {
  return role === "superadmin";
}

const PLATFORM_TENANTS_HREF = "/app/platform/tenants";
/**
 * "Platform" -> "Plans" (plan-admin-ui, TASK.md M2) — a SECOND entry in the
 * SAME allowlist-gated group, alongside "Tenants". Same `showPlatformNav`
 * gate, same fail-CLOSED semantics; zero changes to the "Tenants" entry
 * above, `visibleItems()`, or any of the 19 `NAV_ITEMS`.
 */
const PLATFORM_PLANS_HREF = "/app/platform/plans";

function PlatformNavGroup({
  activePath,
  collapsed,
}: {
  activePath?: string;
  collapsed: boolean;
}) {
  return (
    <SidebarGroup>
      <SidebarGroupLabel>Platform</SidebarGroupLabel>
      <SidebarItem
        href={PLATFORM_TENANTS_HREF}
        active={activePath === PLATFORM_TENANTS_HREF}
        icon={<ShieldCheck className="size-4" />}
      >
        <span className={collapsed ? "sr-only" : undefined}>Tenants</span>
      </SidebarItem>
      <SidebarItem
        href={PLATFORM_PLANS_HREF}
        active={activePath === PLATFORM_PLANS_HREF}
        icon={<Tags className="size-4" />}
      >
        <span className={collapsed ? "sr-only" : undefined}>Plans</span>
      </SidebarItem>
    </SidebarGroup>
  );
}

/**
 * Render the nav links. When `collapsed`, the label text is visually hidden (sr-only) but
 * REMAINS the link's accessible name — an item is never reduced to an icon without a name.
 */
function NavLinks({
  items,
  activePath,
  collapsed,
}: {
  items: NavItem[];
  activePath?: string;
  collapsed: boolean;
}) {
  return (
    <>
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <SidebarItem
            key={item.href}
            href={item.href}
            active={activePath === item.href}
            icon={<Icon className="size-4" />}
          >
            <span className={collapsed ? "sr-only" : undefined}>{item.label}</span>
          </SidebarItem>
        );
      })}
    </>
  );
}

/**
 * The full grouped primary nav — SHARED by the desktop rail and the mobile sheet so the
 * two never drift. Renders each non-empty workflow group (labeled), then a hairline +
 * the pinned Settings item, then the superadmin-only Platform group. When `collapsed`,
 * the group labels are visually hidden (sr-only) but stay in the a11y tree.
 */
function GroupedNav({
  role,
  activePath,
  collapsed,
}: {
  role?: string | null;
  activePath?: string;
  collapsed: boolean;
}) {
  return (
    <>
      {visibleGroups(role).map((group) => (
        <SidebarGroup key={group.label}>
          <SidebarGroupLabel className={collapsed ? "sr-only" : undefined}>
            {group.label}
          </SidebarGroupLabel>
          <NavLinks items={group.items} activePath={activePath} collapsed={collapsed} />
        </SidebarGroup>
      ))}
      <SidebarGroup>
        {/* hairline separating the pinned utility item from the workflow groups above */}
        <div className="mx-3 mb-2 border-t border-sidebar-border" aria-hidden="true" />
        <NavLinks items={[SETTINGS_ITEM]} activePath={activePath} collapsed={collapsed} />
      </SidebarGroup>
      {showPlatformNav(role) ? (
        <PlatformNavGroup activePath={activePath} collapsed={collapsed} />
      ) : null}
    </>
  );
}

export function AppShell({ children, activePath, role, userEmail, banner, commandPalette }: AppShellProps) {
  const [collapsed, setCollapsed] = React.useState(false);
  const brandIcon = <Hexagon className="size-5" />;
  const mainRef = React.useRef<HTMLElement>(null);

  // Fixed-viewport shell (v54): <main> is the desktop scroll container, so Next's
  // window-scroll restoration (window.scrollTo(0,0) on navigation) is a no-op here.
  // Reset the main scroll region to the top when the route changes, or a new page
  // would inherit the previous page's scroll offset. (`scrollTo` is optional-chained
  // so jsdom / older engines without it degrade silently.)
  React.useEffect(() => {
    mainRef.current?.scrollTo?.({ top: 0 });
  }, [activePath]);

  // Global sign-out: POST the BFF logout (clears the HttpOnly session cookie) then
  // hard-navigate to /login. We use window.location (not next/navigation) so the shell
  // stays decoupled from the router — the cookie is gone, so a full reload is correct.
  // Failures are swallowed: the user must always be able to leave.
  async function handleLogout() {
    try {
      await bffAuthPost("logout", {});
    } catch {
      // ignore — sign out client-side regardless of the network result
    }
    window.location.assign("/login");
  }

  // One logout control, rendered in both the desktop footer and the mobile sheet so
  // every layout can sign out (below `lg` the desktop rail is hidden). `iconOnly`
  // collapses it to an icon with an sr-only label (the collapsed rail).
  const logoutButton = (iconOnly: boolean) => (
    <button
      type="button"
      onClick={handleLogout}
      className={cn(
        "inline-flex items-center gap-2 rounded-md border border-border bg-card px-2 py-1.5 text-sm text-sidebar-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        iconOnly && "justify-center",
      )}
    >
      <LogOut className="size-4" aria-hidden="true" />
      <span className={iconOnly ? "sr-only" : undefined}>Log out</span>
    </button>
  );

  return (
    <div className="min-h-screen bg-background text-foreground">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Skip to main content
      </a>

      {/* impersonation-ui M6: rendered AFTER the skip-link (so it stays the first
          focusable element) and BEFORE the fixed-viewport row. Absent by default —
          every existing call site renders nothing extra here. */}
      {banner}

      {/* command-palette M2: rendered immediately after {banner}, before the Dialog
          wrapper below — see the commandPalette prop's own docblock above for why
          this position carries no layout effect. Absent by default (every
          non-superadmin session) — every existing call site renders nothing extra
          here either. */}
      {commandPalette}

      <Dialog>
        {/* Fixed-viewport app shell (v54): below lg the page scrolls as one document
            (min-h-screen, stacked). From lg the row is EXACTLY the viewport height and
            clips its own overflow, so the rail spans full height (lg:h-full, no sticky)
            and <main> owns the only scroll region — guaranteed full-height on any
            browser/zoom, no double scrollbar.
            impersonation-ui M6: the row's OWN height class is CONDITIONAL — the
            literal "lg:h-screen" string when no banner is passed (byte-identical to
            every existing call site), a calc-based reservation only when one is, so
            banner-height + row-height always total exactly 100vh at the lg breakpoint
            (no overlap, no extra page-level scroll). Never an always-on calc class —
            that would silently break the frozen `toContain("lg:h-screen")` assertion
            (tests/design-system/app-shell-sidebar.test.tsx) for every banner-less
            call site. */}
        <div
          className={cn(
            "flex min-h-screen flex-col lg:flex-row lg:overflow-hidden",
            banner ? "lg:h-[calc(100vh-2.75rem)]" : "lg:h-screen",
          )}
        >
          {/* Mobile header — visible below the lg breakpoint; opens the nav sheet. */}
          <header className="flex items-center justify-between gap-2 border-b border-border bg-card p-3 lg:hidden">
            <SidebarBrand title={BRAND} icon={brandIcon} />
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <DialogTrigger asChild>
                <button
                  type="button"
                  aria-label="Open navigation"
                  className="inline-flex size-9 items-center justify-center rounded-md border border-border bg-card text-foreground transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <Menu className="size-4" aria-hidden="true" />
                </button>
              </DialogTrigger>
            </div>
          </header>

          {/* Desktop rail — the single Primary nav landmark; collapsible from the lg breakpoint up.
              lg:h-full makes it span the fixed-viewport-height row (above) at full height with
              NO position:sticky dependency (which can break under an ancestor transform/zoom);
              SidebarContent already scrolls the nav internally when it overflows. */}
          <Sidebar
            aria-label="Primary"
            data-state={collapsed ? "collapsed" : "expanded"}
            className={cn(
              "hidden lg:flex lg:h-full",
              collapsed ? "w-16" : "w-64",
            )}
          >
            <SidebarHeader className={collapsed ? "justify-center" : undefined}>
              {collapsed ? null : <SidebarBrand title={BRAND} icon={brandIcon} />}
              <SidebarTrigger
                aria-expanded={!collapsed}
                onClick={() => setCollapsed((c) => !c)}
              />
            </SidebarHeader>
            <SidebarContent>
              <GroupedNav role={role} activePath={activePath} collapsed={collapsed} />
            </SidebarContent>
            <SidebarFooter>
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2">
                  {userEmail ? (
                    <div className={cn("min-w-0", collapsed && "sr-only")}>
                      <div className="truncate text-sm font-medium text-sidebar-foreground">{userEmail}</div>
                      {role ? (
                        <div className="truncate text-xs capitalize text-muted-foreground">{role}</div>
                      ) : null}
                    </div>
                  ) : (
                    <span className={cn("text-xs text-muted-foreground", collapsed && "sr-only")}>
                      Not signed in
                    </span>
                  )}
                  <ThemeToggle />
                </div>
                {logoutButton(collapsed)}
              </div>
            </SidebarFooter>
          </Sidebar>

          {/* From lg, main is the ONLY scroll region (lg:h-full + lg:overflow-y-auto) so the
              rail/footer stay put. Content stays FLUID (no max-width cap); 2xl:px-16 grows the
              gutters on very wide monitors so content is not edge-glued (wide tables keep width). */}
          <main
            id="main"
            ref={mainRef}
            tabIndex={-1}
            className="flex-1 bg-background p-4 lg:p-8 lg:h-full lg:overflow-y-auto 2xl:px-16 focus:outline-none"
          >
            {/* Reveal = a route-keyed, motion-safe entrance: it remounts on navigation
                (key=activePath) so each admin route fades/slides in. Children render
                unconditionally and the landmark is unchanged — reduced motion shows them
                immediately (motion-safe variant + the global net). */}
            <Reveal key={activePath ?? "shell"}>{children}</Reveal>
          </main>
        </div>

        {/* Mobile nav sheet — portal-mounted only when open, so the default render keeps ONE
            Primary landmark. Its nav is labelled "Site" (distinct from the rail's "Primary"). */}
        <DialogContent className="left-0 top-0 h-full max-w-xs translate-x-0 translate-y-0 rounded-none rounded-r-lg sm:max-w-xs">
          <DialogTitle>Navigation</DialogTitle>
          <DialogDescription className="sr-only">Primary site navigation</DialogDescription>
          <nav aria-label="Site" className="mt-2 flex flex-col gap-1">
            <GroupedNav role={role} activePath={activePath} collapsed={false} />
          </nav>
          <div className="mt-4 border-t border-border pt-4">{logoutButton(false)}</div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
