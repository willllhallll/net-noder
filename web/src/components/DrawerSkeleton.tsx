// Placeholder content shown inside the right-side drawer while its detail data is in
// flight (connection stacks / endpoint detail). A few shimmer bars approximating the
// real panel's shape so the drawer feels populated, not empty, during the wait.
export default function DrawerSkeleton() {
  return (
    <div className="drawer-skeleton">
      <div className="skeleton skel-title" />
      <div className="skeleton skel-line" />
      <div className="skeleton skel-line short" />
      <div className="skeleton skel-block" />
      <div className="skeleton skel-line" />
      <div className="skeleton skel-line short" />
      <div className="skeleton skel-block" />
    </div>
  );
}
