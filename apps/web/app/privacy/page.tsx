import type { Metadata } from "next";
import LegalPage from "../legal/LegalPage";

export const metadata: Metadata = {
  title: "Política de Privacidade — AI Workspace",
  description: "Como o AI Workspace trata seus dados.",
};

export default function Privacy() {
  return <LegalPage file="privacy" />;
}
