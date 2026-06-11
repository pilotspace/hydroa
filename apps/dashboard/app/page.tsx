import { redirect } from "next/navigation";

export const metadata = { title: "Hydroa" };

export default function RootPage() {
  redirect("/login");
}
