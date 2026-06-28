/**
 * tests/chat-message-markdown.test.tsx — MessageMarkdown (chat full-mockup-parity).
 *
 * The design prototype calls for assistant replies rendered as Markdown with
 * syntax-highlighted code blocks (markdown:true, code:true). This pins that
 * contract: GitHub-flavored Markdown renders to semantic HTML, fenced code blocks
 * get a Copy affordance, and raw HTML is NOT executed (XSS-safe by construction —
 * no rehype-raw).
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageMarkdown } from "@/components/chat/MessageMarkdown";

describe("MessageMarkdown", () => {
  it("renders bold, lists, and inline code as semantic HTML", () => {
    const { container } = render(
      <MessageMarkdown content={"**bold** and `inline`\n\n- one\n- two"} />,
    );
    expect(container.querySelector("strong")).toHaveTextContent("bold");
    expect(container.querySelector("code")).toHaveTextContent("inline");
    expect(container.querySelectorAll("li")).toHaveLength(2);
  });

  it("renders a fenced code block with a Copy affordance", () => {
    const md = "```python\nprint('hi')\n```";
    const { container } = render(<MessageMarkdown content={md} />);
    // code block present (highlight.js applies the hljs class)
    expect(container.querySelector("pre code")).not.toBeNull();
    expect(container.textContent).toContain("print");
    // a Copy button is exposed for the block
    expect(screen.getByRole("button", { name: /copy/i })).toBeInTheDocument();
  });

  it("does NOT execute raw HTML (no rehype-raw) — XSS-safe", () => {
    const { container } = render(
      <MessageMarkdown content={"<img src=x onerror=alert(1) />\n\nplain"} />,
    );
    // The raw <img> is NOT rendered as an element — it is escaped/ignored.
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("plain");
  });
});
