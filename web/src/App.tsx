import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { JobsView } from "./pages/JobsView";
import { NewRun } from "./pages/NewRun";
import { RigorCompare } from "./pages/RigorCompare";
import { RunsBrowser } from "./pages/RunsBrowser";
import { WalkForwardDetail } from "./pages/WalkForwardDetail";

export function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<RunsBrowser />} />
        <Route path="/new" element={<NewRun />} />
        <Route path="/jobs" element={<JobsView />} />
        <Route path="/walkforward/:runId" element={<WalkForwardDetail />} />
        <Route path="/evaluate/:runId" element={<RigorCompare />} />
        <Route path="*" element={<p>Not found.</p>} />
      </Routes>
    </Layout>
  );
}
