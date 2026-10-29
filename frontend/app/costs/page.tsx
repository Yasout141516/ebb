"use client";

import Header from "@/components/Header";
import HealthGate from "@/components/HealthGate";
import CostsView from "@/components/costs/CostsView";
import styles from "./page.module.css";

/** The dev-only Costs page. While the API has it off, its routes 404 and the page says "Not found." */
export default function Costs() {
  return (
    <div className={styles.page}>
      <Header />
      <HealthGate>
        <CostsView />
      </HealthGate>
    </div>
  );
}
