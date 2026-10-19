import { redirect } from "next/navigation";

export default function PaymentsPage() {
  redirect("/account/rent-payments?tab=HISTORY");
}
