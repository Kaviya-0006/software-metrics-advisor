import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main(): Promise<void> {
  const demo = await prisma.project.create({
    data: {
      name: 'demo_python',
      status: 'analyzed',
      languages: 'python',
      filesAnalyzed: 3,
      totalClasses: 4,
      totalFunctions: 14,
      totalLoc: 179,
      healthScore: 78.5,
      healthStatus: 'HEALTHY',
      categoryScores: JSON.stringify({ complexity: 80, maintainability: 70 }),
      modules: {
        create: [
          {
            fileName: 'auth.py',
            filePath: 'auth.py',
            language: 'python',
            locTotal: 68,
            locCode: 50,
            locComments: 0,
            locBlank: 18,
            complexityTotal: 12,
            complexityAverage: 2.4,
            complexityMaximum: 5,
            maintainabilityIndex: 71.2,
            riskScore: 34.5,
            riskLevel: 'MEDIUM',
            riskFactors: JSON.stringify(['High cyclomatic complexity']),
          },
        ],
      },
    },
  });
  await prisma.metricSnapshot.create({
    data: { projectId: demo.id, locTotal: 179, complexityTotal: 28, healthScore: 78.5 },
  });
  console.log(`Seeded project ${demo.name} (#${demo.id})`);
}

main()
  .catch((err: unknown) => {
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
