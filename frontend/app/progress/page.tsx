import HomeRedirect from "@/components/HomeRedirect";

// /progress with no subject chosen: the newest subject's dashboard (or onboarding).
export default function Progress() {
  return <HomeRedirect to="/progress" />;
}
