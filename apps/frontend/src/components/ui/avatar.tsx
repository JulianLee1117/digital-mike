import * as React from "react";

function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ");
}

export function Avatar(
  props: React.ImgHTMLAttributes<HTMLImageElement> & { fallback?: string }
) {
  const { className, fallback, ...rest } = props;
  return (
    <div className={cn("inline-flex h-24 w-24 items-center justify-center overflow-hidden rounded-full bg-muted", className)}>
      {/* eslint-disable-next-line jsx-a11y/alt-text */}
      <img className="h-full w-full object-cover" {...rest} />
      {!rest.src && fallback && <span className="text-sm text-muted-foreground">{fallback}</span>}
    </div>
  );
}


