import * as React from "react";

function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export function Card(
  props: React.HTMLAttributes<HTMLDivElement>
) {
  const { className, ...rest } = props;
  return (
    <div
      className={cn(
        "rounded-lg border bg-card text-card-foreground shadow-sm",
        className
      )}
      {...rest}
    />
  );
}

export function CardHeader(
  props: React.HTMLAttributes<HTMLDivElement>
) {
  const { className, ...rest } = props;
  return <div className={cn("flex flex-col space-y-1.5 p-6", className)} {...rest} />;
}

export function CardTitle(
  props: React.HTMLAttributes<HTMLHeadingElement>
) {
  const { className, ...rest } = props;
  return <h3 className={cn("text-2xl font-semibold leading-none tracking-tight", className)} {...rest} />;
}

export function CardDescription(
  props: React.HTMLAttributes<HTMLParagraphElement>
) {
  const { className, ...rest } = props;
  return <p className={cn("text-sm text-muted-foreground", className)} {...rest} />;
}

export function CardContent(
  props: React.HTMLAttributes<HTMLDivElement>
) {
  const { className, ...rest } = props;
  return <div className={cn("p-6 pt-0", className)} {...rest} />;
}

export function CardFooter(
  props: React.HTMLAttributes<HTMLDivElement>
) {
  const { className, ...rest } = props;
  return <div className={cn("flex items-center p-6 pt-0", className)} {...rest} />;
}


