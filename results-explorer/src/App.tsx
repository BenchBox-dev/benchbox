import Router from "preact-router";
import { Layout } from "./components/Layout";
import { Home } from "./pages/Home";
import { BenchmarksIndex } from "./pages/BenchmarksIndex";
import { BenchmarkIndex } from "./pages/BenchmarkIndex";
import { PlatformsIndex } from "./pages/PlatformsIndex";
import { PlatformIndex } from "./pages/PlatformIndex";
import { ResultDetail } from "./pages/ResultDetail";
import { Compare } from "./pages/Compare";
import { CompareWithinRun } from "./pages/CompareWithinRun";
import { QueryRunComparison } from "./pages/QueryRunComparison";
import { Query } from "./pages/Query";
import { NotFound } from "./pages/NotFound";
import { PickingStateProvider } from "./lib/pickingState";
import { LocalResultProvider } from "./lib/localResultState";

export function App() {
  return (
    <LocalResultProvider>
      <PickingStateProvider>
        <Layout>
          <Router>
            <Home path="/results/" />
            <QueryRunComparison path="/results/compare/query/:queryId" />
            <QueryRunComparison path="/results/compare/query/:queryId/" />
            <Compare path="/results/compare" />
            <Compare path="/results/compare/" />
            <Query path="/results/query" />
            <Query path="/results/query/" />
            <BenchmarksIndex path="/results/benchmarks" />
            <BenchmarksIndex path="/results/benchmarks/" />
            <PlatformsIndex path="/results/platforms" />
            <PlatformsIndex path="/results/platforms/" />
            <PlatformIndex path="/results/p/:platform/" />
            <CompareWithinRun path="/results/r/:resultId/passes" />
            <ResultDetail path="/results/r/:resultId" />
            <ResultDetail path="/results/local" source="local" />
            <ResultDetail path="/results/local/" source="local" />
            <ResultDetail path="/results/local/:resultId" source="local" />
            <BenchmarkIndex path="/results/:benchmark/" />
            <NotFound default />
          </Router>
        </Layout>
      </PickingStateProvider>
    </LocalResultProvider>
  );
}
