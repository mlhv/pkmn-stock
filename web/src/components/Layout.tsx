import type { ReactNode } from "react";
import { Link } from "react-router-dom";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div>
      <header>
        <h1>pkmn_quant explorer</h1>
        <nav>
          <Link to="/">Runs</Link>{" "}
          <Link to="/new">New run</Link>{" "}
          <Link to="/jobs">Jobs</Link>
        </nav>
      </header>
      <main>{children}</main>
    </div>
  );
}
