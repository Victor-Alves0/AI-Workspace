import type { Metadata } from "next";
import LegalPage from "../legal/LegalPage";
import { tr } from "@/lib/i18n";

export const metadata: Metadata = {
  title: tr("Termos de Serviço — AI Workspace"),
  description: tr("Regras de uso do AI Workspace."),
};

export default function Terms() {
  return <LegalPage file="terms" />;
}
