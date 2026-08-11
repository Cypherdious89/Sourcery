import { NotebookDetail } from "@/components/NotebookDetail";

// `params` is a Promise in Next.js 16; `PageProps` is a global helper.
export default async function NotebookPage(
  props: PageProps<"/notebooks/[id]">,
) {
  const { id } = await props.params;
  return <NotebookDetail notebookId={id} />;
}
