import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import NewProject from "./pages/NewProject";
import OutlineReview from "./pages/OutlineReview";
import RunDashboard from "./pages/RunDashboard";
import Review from "./pages/Review";
import Downloads from "./pages/Downloads";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/new-project" replace />} />
        <Route path="/new-project" element={<NewProject />} />
        <Route path="/outline" element={<OutlineReview />} />
        <Route path="/dashboard" element={<RunDashboard />} />
        <Route path="/review" element={<Review />} />
        <Route path="/downloads" element={<Downloads />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
}
