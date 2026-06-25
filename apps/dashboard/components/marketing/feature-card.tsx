import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

interface FeatureCardProps {
  title: string;
  description: string;
  icon: string;
}

export function FeatureCard({ title, description, icon }: FeatureCardProps) {
  return (
    <Card className="transition-transform duration-200 ease-standard hover:-translate-y-1 hover:shadow-lg">
      <CardHeader>
        <div
          aria-hidden="true"
          className="mb-3 flex size-12 items-center justify-center rounded-xl border border-accent-soft-border bg-accent-soft text-2xl shadow-sm"
        >
          {icon}
        </div>
        <CardTitle asChild>
          <h3>{title}</h3>
        </CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
    </Card>
  );
}
