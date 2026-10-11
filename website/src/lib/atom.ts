import { atomTimestamp, BLOG_BASE_URL, BLOG_TITLE, feedCategory, FEED_LENGTH, newestFirst, slugify, type BlogPost } from "./blog.ts";

const SITE_ORIGIN = "https://benchbox.dev";
const GENERATOR = { uri: "https://astro.build/", name: "Astro" };

export function feedEntryUrl(post: Pick<BlogPost, "slug">): string {
  return `${BLOG_BASE_URL}${post.slug}.html`;
}

export const FEED_SELF_URL = `${BLOG_BASE_URL}atom.xml`;

export function escapeXml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export function absoluteUrls(html: string): string {
  return html.replace(/\b(href|src)="(\/(?!\/)[^"]*)"/g, (_match, attribute: string, target: string) => `${attribute}="${SITE_ORIGIN}${target}"`);
}

const EXPRESSIVE_CODE = /<div class="expressive-code">[\s\S]*?<\/figure><\/div>/g;

function plainCodeBlock(block: string): string {
  const pre = /<pre\b([^>]*)>([\s\S]*?)<\/pre>/.exec(block);
  if (!pre) return "";
  const language = /data-language="([^"]*)"/.exec(pre[1])?.[1];
  const lines = pre[2]
    .split('<div class="ec-line">')
    .slice(1)
    .map((line) => line.replace(/<[^>]+>/g, ""));
  const attribute = language ? ` class="language-${language}"` : "";
  return `<pre><code${attribute}>${lines.join("\n")}</code></pre>`;
}

export function feedHtml(html: string): string {
  return html
    .replace(EXPRESSIVE_CODE, plainCodeBlock)
    .replace(/<script\b[\s\S]*?<\/script>/g, "")
    .replace(/<link\b[^>]*>/g, "")
    .replace(/<button\b[\s\S]*?<\/button>/g, "")
    .replace(/\sstyle="--[^"]*"/g, "");
}

function plainText(html: string): string {
  return html
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function summaryOf(post: BlogPost): string {
  const paragraph = post.html?.match(/<p[^>]*>([\s\S]*?)<\/p>/)?.[1];
  return paragraph === undefined ? (post.description ?? "") : plainText(paragraph);
}

function entry(post: BlogPost): string {
  const stamp = atomTimestamp(post.date);
  const url = feedEntryUrl(post);
  const lines = [
    "  <entry>",
    `    <id>${escapeXml(url)}</id>`,
    `    <title>${escapeXml(post.title)}</title>`,
    `    <updated>${stamp}</updated>`,
    "    <author>",
    `      <name>${escapeXml(post.author)}</name>`,
    "    </author>",
    `    <content type="html">${escapeXml(absoluteUrls(feedHtml(post.html ?? "")))}</content>`,
    `    <link href="${escapeXml(url)}"/>`,
  ];
  const summary = summaryOf(post);
  if (summary !== "") lines.push(`    <summary>${escapeXml(summary)}</summary>`);
  for (const tag of [...post.tags].sort()) {
    if (slugify(tag) !== "") lines.push(`    <category term="${escapeXml(feedCategory(tag))}" label="${escapeXml(tag)}"/>`);
  }
  lines.push(`    <published>${stamp}</published>`, "  </entry>");
  return lines.join("\n");
}

export function buildAtomFeed(posts: readonly BlogPost[]): string {
  const recent = newestFirst(posts).slice(0, FEED_LENGTH);
  const updated = recent.length > 0 ? atomTimestamp(recent[0].date) : "1970-01-01T00:00:00+00:00";
  return [
    `<?xml version="1.0" encoding="UTF-8"?>`,
    `<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en">`,
    `  <id>${BLOG_BASE_URL}</id>`,
    `  <title>${escapeXml(BLOG_TITLE)}</title>`,
    `  <updated>${updated}</updated>`,
    `  <link href="${BLOG_BASE_URL}"/>`,
    `  <link href="${FEED_SELF_URL}" rel="self"/>`,
    `  <generator uri="${GENERATOR.uri}">${GENERATOR.name}</generator>`,
    ...recent.map(entry),
    `</feed>`,
    "",
  ].join("\n");
}
