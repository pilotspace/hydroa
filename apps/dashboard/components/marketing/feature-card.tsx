import { Card, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";

interface FeatureCardProps {
  title: string;
  description: string;
  icon: string;
}

export function FeatureCard({ title, description, icon }: FeatureCardProps) {
  return (
    <Card>
      <CardHeader>
        <div
          aria-hidden="true"
          className="mb-2 text-3xl"
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
