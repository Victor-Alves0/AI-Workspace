import type { Metadata } from "next";
import LegalPage from "../legal/LegalPage";
import { tr } from "@/lib/i18n";

export const metadata: Metadata = {
  title: tr("Política de Privacidade — AI Workspace"),
  description: tr("Como o AI Workspace trata seus dados."),
};

export default function Privacy() {
  return <LegalPage file="privacy" />;
}
