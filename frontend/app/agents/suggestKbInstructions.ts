// Builds a ready-to-use KB instructions suggestion naming the attached
// document(s) by display name. The backend only lets a call's
// search_knowledge_base tool touch a document if kb_instructions actually
// names it once two or more are selected (app/telephony/shared.py,
// filter_kb_documents_by_instructions) - a generic "use the attached
// documents" line silently makes every document but the first ineligible.
// Naming each one here means the agent is immediately callable about an
// attached document (e.g. a resume) without the person needing to know
// that rule and hand-write the instructions themselves.
export function suggestKbInstructions(docs: { display_name: string; file_name: string }[]): string {
  if (docs.length === 0) return "";
  const names = docs.map(d => `"${d.display_name || d.file_name}"`);
  if (names.length === 1) {
    return `When the caller asks about the attached document ${names[0]}, use its contents as your source of truth and answer naturally. If the answer isn't in the document, say so honestly instead of guessing.`;
  }
  return `When the caller's question relates to one of the attached documents (${names.join(", ")}), use that document's contents as your source of truth and answer naturally. If the answer isn't in the documents, say so honestly instead of guessing.`;
}
